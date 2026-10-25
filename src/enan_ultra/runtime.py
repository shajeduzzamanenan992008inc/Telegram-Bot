"""Application lifecycle and background jobs for long-running deployments."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes, InlineQueryHandler, MessageHandler, filters,
)

from enan_ultra.actions import ActionRegistry
from enan_ultra.config import Settings
from enan_ultra.dashboard import AdminDashboard
from enan_ultra.files.registry import FileRegistry
from enan_ultra.handlers import BotHandlers
from enan_ultra.mini_app import MiniAppServer
from enan_ultra.services.media import MediaProcessor
from enan_ultra.services.openrouter import OpenRouter
from enan_ultra.services.providers import ProviderRouter
from enan_ultra.services.agents import AgentOrchestrator
from enan_ultra.services.code_sandbox import CodeSandbox
from enan_ultra.services.mission_control import MissionControl
from enan_ultra.services.health import resource_metrics
from enan_ultra.services.integrations import HomeAssistantClient, VMControl, install_sanitized_log_buffer
from enan_ultra.services.telegram_zip_updates import TelegramZipUpdateManager
from enan_ultra.sticker_collector import inline_sticker_query
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
    missions = MissionControl(settings, db, router)
    handlers = BotHandlers(settings, db, router, files, media, actions, agents, missions)
    sticker_collector = handlers.sticker_collector
    dashboard = AdminDashboard(settings, db, router, actions, vm_control) if settings.dashboard_enabled else None
    mini_app = MiniAppServer(settings, db, router, handlers, handlers.theme_engine) if settings.mini_app_url else None
    provider_alert_lock = asyncio.Lock()
    application_ref: Application | None = None

    async def send_provider_failure_alert(app: Application, event: dict) -> None:
        if not settings.admin_alerts:
            return
        async with provider_alert_lock:
            now = datetime.now(UTC)
            previous = await db.get_setting("provider_failure_alert_at")
            if previous:
                try:
                    last_sent = datetime.fromisoformat(previous)
                except ValueError:
                    last_sent = None
                if last_sent and now - last_sent < timedelta(minutes=10):
                    return
            await db.set_setting("provider_failure_alert_at", now.isoformat())
            allowed_providers = set(router.adapters)
            failures = [
                f"{item.get('provider')} ({item.get('category')})"
                for item in event.get("failures", [])
                if item.get("provider") in allowed_providers
            ]
            if not failures:
                return
            try:
                await app.bot.send_message(
                    chat_id=settings.admin_chat_id,
                    text=(
                        "⚠️ AI provider failover exhausted.\n"
                        f"Correlation ID: {str(event.get('correlation_id', ''))[:32]}\n"
                        "Provider errors: " + ", ".join(failures[:8])
                        + "\nUse /providerdiag for local circuit status."
                    ),
                )
            except Exception as exc:
                logger.info("Provider failure alert delivery failed (%s)", type(exc).__name__)

    def schedule_provider_failure_alert(event: dict) -> None:
        if application_ref:
            application_ref.create_task(
                send_provider_failure_alert(application_ref, event),
                name="provider-failure-admin-alert",
            )

    async def post_init(app: Application) -> None:
        nonlocal application_ref
        application_ref = app
        router.failure_notifier = schedule_provider_failure_alert
        await db.open()
        recovered_update_gate = await db.recover_update_upload_gate(time.time())
        if recovered_update_gate:
            logger.info(
                "Closed %s interrupted or expired ZIP upload gate(s)",
                recovered_update_gate,
            )
        recovered_missions = await db.recover_missions_after_restart()
        if recovered_missions:
            logger.info("Paused %s interrupted mission(s) for explicit owner resumption", recovered_missions)
        await _migrate_legacy_sticker_catalog(
            db, settings.database_path.with_name("stickers_store.db"),
        )
        app.bot_data["database"] = db
        app.bot_data["sticker_collector"] = sticker_collector
        app.bot_data["style_profiles"] = handlers.style_profiles
        app.bot_data["theme_engine"] = handlers.theme_engine
        app.bot_data["openrouter"] = openrouter
        app.bot_data["provider_router"] = router
        app.bot_data["action_registry"] = actions
        app.bot_data["mission_control"] = missions
        app.bot_data["settings"] = settings
        await handlers.register_commands(app)
        if mini_app:
            try:
                await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                    text="OMEGA AI", web_app=WebAppInfo(url=settings.mini_app_url),
                ))
            except Exception as exc:
                logger.warning("Mini App menu button was not configured (%s)", type(exc).__name__)
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
        if mini_app:
            mini_app_task = app.create_task(mini_app.server.serve(), name="telegram-mini-app")
            app.bot_data["mini_app_task"] = mini_app_task
            for _ in range(50):
                if mini_app.server.started:
                    break
                if mini_app_task.done():
                    error = mini_app_task.exception()
                    raise RuntimeError("Telegram Mini App server failed to start") from error
                await asyncio.sleep(0.1)
            if not mini_app.server.started:
                mini_app.server.should_exit = True
                raise RuntimeError("Telegram Mini App server did not become ready on loopback")
        await handlers.notify_deployment_results(app)
        app.bot_data["maintenance_task"] = app.create_task(
            _maintenance(app, db, router, settings, handlers.update_manager),
        )
        if settings.update_deployment_enabled:
            if not await db.health_check():
                raise RuntimeError("Deployment health gate could not query the application database")
            bot_identity = await app.bot.get_me()
            _write_deployment_health_marker(
                _deployment_health_marker_path(settings.database_path),
                int(bot_identity.id),
            )
        logger.info("Bot startup complete; SQLite WAL enabled; configured access mode=%s", settings.access_mode)

    async def post_shutdown(app: Application) -> None:
        if mini_app:
            mini_app.server.should_exit = True
            task = app.bot_data.get("mini_app_task")
            if task:
                try:
                    await task
                except Exception as exc:
                    logger.info("Mini App shutdown completed with (%s)", type(exc).__name__)
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
    app.add_handler(InlineQueryHandler(inline_sticker_query), group=0)
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_collector.handle), group=0)
    app.add_error_handler(_error_handler)
    return app


def _deployment_health_marker_path(database_path: Path) -> Path:
    return database_path.parent / "runtime-health.json"


def _write_deployment_health_marker(path: Path, telegram_bot_id: int) -> None:
    payload = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "database_open": True,
        "telegram_bot_id": telegram_bot_id,
        "import_origin": str(Path(__file__).resolve()),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.error("Could not clean temporary deployment health marker (%s)", type(exc).__name__)
        raise RuntimeError("Could not write the deployment health marker") from exc


async def _maintenance(
    app: Application,
    db: Database,
    router: ProviderRouter,
    settings: Settings,
    update_manager: TelegramZipUpdateManager | None = None,
) -> None:
    while True:
        try:
            await _monitor_resources(app, db, settings)
            await _scheduled_backup(app, db, settings)
            await db.cleanup_documents()
            await db.cleanup_action_executions()
            await db.cleanup_processed_updates()
            await db.expire_approvals()
            await db.expire_missions()
            await db.expire_evolution_proposals()
            await db.cleanup_mission_history()
            await db.cleanup_evolution_history()
            style_profiles = app.bot_data.get("style_profiles")
            if style_profiles:
                await style_profiles.cleanup_expired()
            theme_engine = app.bot_data.get("theme_engine")
            if theme_engine:
                await theme_engine.cleanup_expired(db)
            if update_manager:
                await asyncio.to_thread(update_manager.cleanup_expired)
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


async def _migrate_legacy_sticker_catalog(db: Database, legacy_path: Path) -> None:
    if await db.get_setting("legacy_sticker_catalog_migrated") == "on":
        return
    if not legacy_path.exists():
        await db.set_setting("legacy_sticker_catalog_migrated", "on")
        return

    def read_legacy_rows() -> list[tuple[str, str, str]]:
        with sqlite3.connect(legacy_path) as connection:
            return [
                (str(row[0]), str(row[1] or ""), str(row[2] or "general"))
                for row in connection.execute(
                    "SELECT file_id,emoji,category FROM auto_stickers ORDER BY id"
                )
            ]

    try:
        rows = await asyncio.to_thread(read_legacy_rows)
        migrated = 0
        for file_id, emoji, category in rows:
            if category not in {
                "greeting", "happy", "celebration", "thanks", "thinking",
                "confused", "encouragement", "supportive", "programming",
                "science", "goodbye", "general", "laugh", "sad", "angry", "love",
            }:
                category = "general"
            migrated += int(await db.save_sticker(
                file_id=file_id, file_unique_id=None, emoji=emoji,
                category=category, source_type="legacy_import",
                approved=False,
            ))
        await db.set_setting("legacy_sticker_catalog_migrated", "on")
        if rows:
            logger.info(
                "Migrated %s legacy sticker metadata record(s) into pending review",
                migrated,
            )
    except sqlite3.Error as exc:
        logger.warning(
            "Legacy sticker catalog migration was not completed (%s)",
            type(exc).__name__,
        )


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
