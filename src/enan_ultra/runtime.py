"""Application lifecycle and background jobs for long-running deployments."""

from __future__ import annotations

import asyncio
import logging
import re
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
from enan_ultra.services.health import resource_metrics
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
    openrouter = OpenRouter(settings) if settings.openrouter_keys else None
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
                BotCommand("model", "Change the current AI model"),
                BotCommand("models", "List models from configured providers"),
                BotCommand("status", "Bot status"),
                BotCommand("end", "End a live session"),
                BotCommand("endall", "End all live sessions"),
                BotCommand("clearuser", "Clear a user's conversation memory"),
                BotCommand("resetuser", "Clear a user's conversation memory"),
                BotCommand("device", "Allowlisted Home Assistant control"),
                BotCommand("vm", "Bot service status/restart"),
                BotCommand("block", "Block a user's bot access"),
                BotCommand("unblock", "Unblock a user's bot access"),
                BotCommand("tasks", "Manage your personal task list"),
                BotCommand("search", "Search recent chat history"),
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
            await _monitor_resources(app, db, settings)
            await _scheduled_backup(app, db, settings)
            await db.cleanup_documents()
            await db.cleanup_action_executions()
            await db.expire_approvals()
            await _cleanup_provider_files(db, router, settings, app)
            for reminder in await db.claim_due_reminders():
                try:
                    await app.bot.send_message(
                        chat_id=reminder["chat_id"], text=f"⏰ Reminder: {reminder['reminder_text']}"
                    )
                    await db.mark_reminder_delivered(int(reminder["id"]))
                except Exception as exc:
                    attempts = int(reminder.get("attempts", 1))
                    retry_after = min(60 * 2 ** min(max(attempts - 1, 0), 10), 86400)
                    await db.defer_reminder(int(reminder["id"]), retry_after)
                    logger.warning("Reminder delivery deferred; attempt=%s retry_in=%ss (%s)",
                                   attempts, retry_after, type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Maintenance cycle failed (%s)", type(exc).__name__)
        await asyncio.sleep(60)


async def _monitor_resources(app: Application, db: Database, settings: Settings) -> None:
    resources = resource_metrics(db.path)
    alerts: list[str] = []
    if resources["disk_free_bytes"] < 1024 * 1024 * 1024:
        free_gb = resources["disk_free_bytes"] / (1024 ** 3)
        alerts.append(f"Only {free_gb:.2f} GiB is free on the database volume.")
    memory_used = resources["memory_used_percent"]
    if memory_used is not None and memory_used >= 95:
        alerts.append(f"System memory usage is {memory_used:.1f}%.")
    if not alerts:
        return
    logger.warning("Resource pressure detected: %s", "; ".join(alerts))
    if not settings.admin_alerts:
        return
    now = datetime.now(UTC)
    previous_value = await db.get_setting("last_resource_alert")
    previous = datetime.fromisoformat(previous_value) if previous_value else None
    if previous is not None and now - previous < timedelta(hours=6):
        return
    try:
        await app.bot.send_message(
            settings.admin_chat_id,
            "⚠️ Server resource alert:\n" + "\n".join(alerts),
        )
        await db.set_setting("last_resource_alert", now.isoformat())
    except Exception as exc:
        logger.warning("Resource alert delivery failed (%s)", type(exc).__name__)


async def _scheduled_backup(app: Application, db: Database, settings: Settings) -> None:
    now = datetime.now(UTC)
    previous_value = await db.get_setting("last_scheduled_backup")
    previous = datetime.fromisoformat(previous_value) if previous_value else None
    if previous is not None and now - previous < timedelta(hours=24):
        return
    directory = db.path.parent / "backups"
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    destination = directory / f"{db.path.stem}-{timestamp}.sqlite3.enc"
    try:
        await db.backup_encrypted_to(destination)
        await db.set_setting("last_scheduled_backup", now.isoformat())
        pattern = re.compile(rf"^{re.escape(db.path.stem)}-\d{{8}}T\d{{6}}Z\.sqlite3\.enc$")
        backups = sorted(
            (path for path in directory.iterdir() if path.is_file() and pattern.fullmatch(path.name)),
            key=lambda path: path.name,
            reverse=True,
        )
        for stale in backups[7:]:
            stale.unlink()
        logger.info("Encrypted scheduled database backup completed")
    except Exception as exc:
        logger.error("Scheduled database backup failed (%s)", type(exc).__name__)
        if not settings.admin_alerts:
            return
        previous_alert = await db.get_setting("last_scheduled_backup_alert")
        last_alert = datetime.fromisoformat(previous_alert) if previous_alert else None
        if last_alert is None or now - last_alert >= timedelta(hours=6):
            try:
                await app.bot.send_message(
                    settings.admin_chat_id,
                    "⚠️ The scheduled encrypted database backup failed. Check server storage and the APP_DATA_KEY configuration.",
                )
                await db.set_setting("last_scheduled_backup_alert", now.isoformat())
            except Exception as alert_exc:
                logger.warning("Scheduled backup alert failed (%s)", type(alert_exc).__name__)


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
                    "⚠️ One or more temporary files could not be deleted from OpenRouter. The bot is retrying; check cleanup in the provider dashboard.",
                )
                await db.set_setting("last_provider_cleanup_alert", current.isoformat())
        except Exception as exc:
            logger.info("Admin cleanup alert could not be sent (%s)", type(exc).__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error("Unhandled Telegram update error (%s)", type(error).__name__ if error else "unknown")
