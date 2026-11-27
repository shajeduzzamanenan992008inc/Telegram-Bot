"""Background maintenance loop isolation and embedded web server behaviour."""

from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conftest import ROOT
from enan_ultra import runtime
from enan_ultra.embedded_server import build_embedded_server


def make_environment(monkeypatch, calls):
    """Fake application/database; the loop is cancelled when it would sleep after one cycle."""

    async def stop_after_first_cycle(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(
        sleep=stop_after_first_cycle, CancelledError=asyncio.CancelledError, to_thread=asyncio.to_thread,
    ))

    def recorder(name):
        async def record(*args, **kwargs):
            calls.append(name)
        return record

    for helper in ("_monitor_resources", "_scheduled_backup", "_cleanup_provider_files"):
        monkeypatch.setattr(runtime, helper, recorder(helper))

    db = SimpleNamespace()
    for name in ("cleanup_documents", "cleanup_action_executions", "cleanup_processed_updates", "expire_approvals",
                 "expire_missions", "expire_evolution_proposals", "cleanup_mission_history",
                 "cleanup_evolution_history", "mark_reminder_delivered", "defer_reminder"):
        setattr(db, name, AsyncMock(side_effect=recorder(name)))

    async def claim_due_reminders():
        calls.append("reminders")
        return [{"id": 7, "chat_id": 10, "reminder_text": "stand up", "attempts": 1}]

    db.claim_due_reminders = claim_due_reminders
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), bot_data={})
    return app, db


def test_a_failing_step_does_not_block_reminders_or_later_steps(monkeypatch):
    """Regression: every step shared one try/except, so one failure starved reminder delivery."""
    calls: list[str] = []
    app, db = make_environment(monkeypatch, calls)
    db.cleanup_documents = AsyncMock(side_effect=RuntimeError("disk is full"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintenance(app, db, router=None, settings=None))
    app.bot.send_message.assert_awaited_once_with(chat_id=10, text="⏰ Reminder: stand up")
    assert "mark_reminder_delivered" in calls
    assert "cleanup_processed_updates" in calls, "steps after the failing one must still run"
    assert "_cleanup_provider_files" in calls


def test_reminders_are_delivered_before_slow_housekeeping(monkeypatch):
    calls: list[str] = []
    app, db = make_environment(monkeypatch, calls)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintenance(app, db, router=None, settings=None))
    assert calls[0] == "reminders"
    assert calls.index("mark_reminder_delivered") < calls.index("_monitor_resources")


def test_failed_reminder_delivery_is_deferred_with_backoff(monkeypatch):
    calls: list[str] = []
    app, db = make_environment(monkeypatch, calls)
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("chat not found"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintenance(app, db, router=None, settings=None))
    db.defer_reminder.assert_awaited_once_with(7, 60)
    assert "mark_reminder_delivered" not in calls


def test_embedded_servers_leave_process_signal_handlers_alone():
    """Regression: uvicorn >= 0.29 ignores install_signal_handlers and would replace the bot's handlers."""
    server = build_embedded_server(object(), host="127.0.0.1", port=0)
    before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    with server.capture_signals():
        assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


def test_dashboard_and_mini_app_use_the_shared_server_builder():
    for name in ("dashboard.py", "mini_app.py"):
        source = (ROOT / "src" / "enan_ultra" / name).read_text(encoding="utf-8")
        assert "build_embedded_server(" in source
        assert "install_signal_handlers" not in source
