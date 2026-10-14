"""Application lifecycle and background jobs for long-running deployments."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from telegram import BotCommand, BotCommandScopeChat
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from enan_ultra.actions import ActionRegistry
from enan_ultra.config import Settings
from enan_ultra.dashboard import AdminDashboard
from enan_ultra.files.registry import FileRegistry
from enan_ultra.handlers import BotHandlers
from enan_ultra.services.media import MediaProcessor
from enan_ultra.services.openrouter import OpenRouter
from enan_ultra.services.providers import ProviderRouter
from enan_ultra.services.agents import AgentOrchestrator
from enan_ultra.services.code_sandbox import CodeSandbox
from enan_ultra.services.integrations import HomeAssistantClient, VMControl, install_sanitized_log_buffer
from enan_ultra.storage.database import Database

logger = logging.getLogger(__name__)


def build_application(settings: Settings) -> Application:
    install_sanitized_log_buffer()
    if bool(settings.local_bot_api_base_url) != bool(settings.local_bot_api_file_url):
        raise ValueError("LOCAL_BOT_API_BASE_URL and LOCAL_BOT_API_FILE_URL must be configured together")
    db = Database(
        settings.database_path, settings.document_retention_hours,
        settings.max_pending_reminders, settings.app_data_key,
        settings.kb_max_documents, settings.kb_max_bytes,
    )
    openrouter = OpenRouter(settings) if settings.openrouter_key else None
    router = ProviderRouter(settings, openrouter)
    home_assistant = HomeAssistantClient(settings) if settings.ha_base_url else None
    vm_control = VMControl()
    sandbox = CodeSandbox(settings)
    files = FileRegistry(router)
    media = MediaProcessor(settings, router)
    actions = ActionRegistry(settings, home_assistant=home_assistant, vm_control=vm_control, sandbox=sandbox)
    agents = AgentOrchestrator(settings, db, router)
    handlers = BotHandlers(settings, db, router, files, media, actions, agents)
    dashboard = AdminDashboard(settings, db, router, actions, vm_control) if settings.dashboard_enabled else None

    async def post_init(app: Application) -> None:
        await db.open()
        app.bot_data["database"] = db
        app.bot_data["openrouter"] = openrouter
        app.bot_data["provider_router"] = router
        app.bot_data["action_registry"] = actions
        app.bot_data["settings"] = settings
        await handlers.register_commands(app)
        try:
            admin_commands = [
                BotCommand("model", "বর্তমান AI model বদলান"),
                BotCommand("models", "Configured provider model তালিকা"),
                BotCommand("status", "Bot status"),
                BotCommand("end", "একটি live session বন্ধ"),
                BotCommand("endall", "সব live session বন্ধ"),
                BotCommand("device", "Allowlisted Home Assistant control"),
                BotCommand("vm", "Bot service status/restart"),
            ]
            await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(settings.admin_chat_id))
        except Exception as exc:
            logger.info("Admin command menu was not updated (%s)", type(exc).__name__)
        # Retry interrupted provider file deletions immediately after a service restart.
        await _cleanup_provider_files(db, router, settings, app)
        if dashboard:
            dashboard_task = app.create_task(dashboard.server.serve(), name="admin-dashboard")
            app.bot_data["dashboard_task"] = dashboard_task
            for _ in range(50):
                if dashboard.server.started:
                    break
                if dashboard_task.done():
                    error = dashboard_task.exception()
                    raise RuntimeError("Admin dashboard failed to start") from error
                await asyncio.sleep(0.1)
            if not dashboard.server.started:
                dashboard.server.should_exit = True
                raise RuntimeError("Admin dashboard did not become ready on loopback")
        app.bot_data["maintenance_task"] = app.create_task(_maintenance(app, db, router, settings))
        logger.info("Bot startup complete; SQLite WAL enabled; configured access mode=%s", settings.access_mode)

    async def post_shutdown(app: Application) -> None:
        if dashboard:
            dashboard.server.should_exit = True
            task = app.bot_data.get("dashboard_task")
            if task:
                try:
                    await task
                except Exception as exc:
                    logger.info("Dashboard shutdown completed with (%s)", type(exc).__name__)
        task = app.bot_data.get("maintenance_task")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await router.close()
        if home_assistant:
            await home_assistant.close()
        await db.close()
        logger.info("Bot shutdown complete")

    builder: ApplicationBuilder = Application.builder().token(settings.telegram_token)
    builder.post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(16)
    if settings.local_bot_api_base_url:
        builder.base_url(settings.local_bot_api_base_url).base_file_url(settings.local_bot_api_file_url)
    app = builder.build()
    handlers.install(app)
    app.add_error_handler(_error_handler)
    return app


async def _maintenance(app: Application, db: Database, router: ProviderRouter, settings: Settings) -> None:
    while True:
        try:
            await db.cleanup_documents()
            await db.cleanup_action_executions()
            await db.expire_approvals()
            await _cleanup_provider_files(db, router, settings, app)
            for reminder in await db.due_reminders():
                try:
                    await app.bot.send_message(
                        chat_id=reminder["chat_id"], text=f"⏰ রিমাইন্ডার: {reminder['reminder_text']}"
                    )
                    await db.mark_reminder_delivered(int(reminder["id"]))
                except Exception as exc:
                    logger.info("Reminder delivery deferred (%s)", type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Maintenance cycle failed (%s)", type(exc).__name__)
        await asyncio.sleep(60)


async def _cleanup_provider_files(db: Database, router: ProviderRouter, settings: Settings, app: Application) -> None:
    failed = False
    for file_id in await db.pending_provider_files():
        try:
            await router.delete_file(file_id)
            await db.clear_provider_file(file_id)
        except Exception as exc:
            failed = True
            logger.warning("Provider file cleanup remains pending (%s)", type(exc).__name__)
    if failed and settings.admin_alerts:
        try:
            current = datetime.now(UTC)
            previous = await db.get_setting("last_provider_cleanup_alert")
            last_sent = datetime.fromisoformat(previous) if previous else None
            if last_sent is None or current - last_sent >= timedelta(hours=1):
                await app.bot.send_message(
                    settings.admin_chat_id,
                    "⚠️ OpenRouter-এ একটি বা একাধিক temporary file delete করা যায়নি। Bot retry করছে; provider dashboard-এ cleanup দেখুন।",
                )
                await db.set_setting("last_provider_cleanup_alert", current.isoformat())
        except Exception as exc:
            logger.info("Admin cleanup alert could not be sent (%s)", type(exc).__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error("Unhandled Telegram update error (%s)", type(error).__name__ if error else "unknown")
