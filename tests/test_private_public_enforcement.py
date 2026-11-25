"""Integration tests for Private/Public mode enforcement across entry points."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from enan_ultra.access_control import AccessControlService
from enan_ultra.config import Settings
from enan_ultra.handlers import BotHandlers
from enan_ultra.storage.database import Database

TOKEN = "123456:private-public-enforcement-token-long-enough"
APP_KEY = "k" * 64
ADMIN_ID = 100
ORDINARY_ID = 200


def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access_mode: str) -> Settings:
    monkeypatch.chdir(tmp_path)
    values = {
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": str(ADMIN_ID),
        "ALLOWED_USER_IDS": "",
        "OPENROUTER_API_KEY": "test-provider-key",
        "APP_DATA_KEY": APP_KEY,
        "DASHBOARD_AUTH_TOKEN": "test-dashboard-auth-token-12345678901234567890",
        "ACCESS_MODE": access_mode,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings.load()
    assert settings.access_mode == access_mode
    return settings


class _Message:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _Query:
    def __init__(self, data: str = "main:help") -> None:
        self.answers: list[tuple[str | None, bool]] = []
        self.data = data
        self.message = _Message()

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def _command_update(message: _Message, user_id: int, chat_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, full_name="User"),
        effective_chat=SimpleNamespace(id=chat_id if chat_id is not None else user_id, type="private"),
        effective_message=message,
    )


def test_setmode_dispatches_global_and_chat_modes_without_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, monkeypatch, "public")

    async def scenario() -> None:
        db = Database(tmp_path / "dispatch.sqlite3")
        await db.open()
        try:
            handlers = BotHandlers(settings, db, None, None, None, None)
            admin_message = _Message()
            admin_update = _command_update(admin_message, ADMIN_ID)

            # Admin: /setmode public changes the global mode.
            await handlers.set_mode(admin_update, SimpleNamespace(args=["public"]))
            assert await db.get_global_access_mode() == "public"
            assert "PUBLIC" in admin_message.replies[-1]

            # Admin: /setmode <chat style> no longer collides with global mode.
            await handlers.set_mode(admin_update, SimpleNamespace(args=["sarcastic"]))
            assert await db.get_chat_mode(ADMIN_ID) == "sarcastic"
            assert await db.get_global_access_mode() == "public"

            # Non-admin cannot change the global mode, but the chat mode works.
            ordinary_message = _Message()
            ordinary_update = _command_update(ordinary_message, ORDINARY_ID)
            await handlers.set_mode(ordinary_update, SimpleNamespace(args=["private"]))
            assert await db.get_global_access_mode() == "public"
            assert "administrators only" in ordinary_message.replies[-1]
            await handlers.set_mode(ordinary_update, SimpleNamespace(args=["anime"]))
            assert await db.get_chat_mode(ORDINARY_ID) == "anime"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_gate_denies_ordinary_commands_and_messages_in_private_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, monkeypatch, "public")

    async def scenario() -> None:
        db = Database(tmp_path / "gate.sqlite3")
        await db.open()
        try:
            handlers = BotHandlers(settings, db, None, None, None, None)
            ordinary_message = _Message()
            ordinary_update = _command_update(ordinary_message, ORDINARY_ID)
            assert await handlers._gate(ordinary_update) is False
            assert "Private Mode" in ordinary_message.replies[-1]

            admin_message = _Message()
            admin_update = _command_update(admin_message, ADMIN_ID)
            assert await handlers._gate(admin_update) is True

            # A mode switch restores ordinary access through the same gate.
            await db.set_global_access_mode("public", ADMIN_ID)
            assert await handlers._gate(ordinary_update) is True
        finally:
            await db.close()

    asyncio.run(scenario())


def test_menu_callback_queries_respect_global_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, monkeypatch, "public")

    async def scenario() -> None:
        db = Database(tmp_path / "callback.sqlite3")
        await db.open()
        try:
            handlers = BotHandlers(settings, db, None, None, None, None)
            denied_query = _Query()
            denied_update = SimpleNamespace(
                callback_query=denied_query,
                effective_user=SimpleNamespace(id=ORDINARY_ID),
                effective_chat=SimpleNamespace(id=ORDINARY_ID, type="private"),
            )
            await handlers.menu_callback(denied_update, SimpleNamespace())
            assert denied_query.answers == [
                ("You are not authorized to use this bot.", True),
            ]
            assert denied_query.message.replies == []

            await db.set_global_access_mode("public", ADMIN_ID)
            allowed_query = _Query()
            allowed_update = SimpleNamespace(
                callback_query=allowed_query,
                effective_user=SimpleNamespace(id=ORDINARY_ID),
                effective_chat=SimpleNamespace(id=ORDINARY_ID, type="private"),
            )
            await handlers.menu_callback(allowed_update, SimpleNamespace())
            assert allowed_query.answers == [(None, False)]
            assert allowed_query.message.replies
        finally:
            await db.close()

    asyncio.run(scenario())


def test_two_database_connections_share_authoritative_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, monkeypatch, "public")

    async def scenario() -> None:
        path = tmp_path / "shared.sqlite3"
        first = Database(path)
        second = Database(path)
        await first.open()
        await second.open()
        try:
            service = AccessControlService(settings, first)
            assert (await service.is_access_allowed(ORDINARY_ID))[0] is False

            await second.set_global_access_mode("public", ADMIN_ID)
            assert await first.get_global_access_mode() == "public"
            assert await service.is_access_allowed(ORDINARY_ID) == (True, "allowed")

            await first.set_global_access_mode("private", ADMIN_ID)
            assert await second.get_global_access_mode() == "private"
            assert await service.is_access_allowed(ORDINARY_ID) == (False, "private_mode")
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_concurrent_mode_changes_never_leave_an_unknown_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, monkeypatch, "public")

    async def scenario() -> None:
        path = tmp_path / "concurrent.sqlite3"
        first = Database(path)
        second = Database(path)
        await first.open()
        await second.open()
        try:
            results = await asyncio.gather(
                first.set_global_access_mode("private", ADMIN_ID),
                second.set_global_access_mode("public", ADMIN_ID),
                return_exceptions=True,
            )
            errors = [item for item in results if isinstance(item, Exception)]
            assert not errors, [type(item).__name__ for item in errors]
            mode = await first.get_global_access_mode()
            assert mode in {"private", "public"}
            service = AccessControlService(settings, first)
            allowed, reason = await service.is_access_allowed(ORDINARY_ID)
            assert allowed is (mode == "public")
            assert reason == ("allowed" if mode == "public" else "private_mode")
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_mode_read_failure_denies_ordinary_users_and_keeps_admin_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, monkeypatch, "public")

    class FailingDatabase:
        async def get_global_access_mode(self) -> str:
            raise RuntimeError("disk I/O error")

        async def is_user_blocked(self, user_id: int) -> bool:
            return False

    async def scenario() -> None:
        service = AccessControlService(settings, FailingDatabase())
        assert await service.is_access_allowed(ORDINARY_ID) == (False, "mode_unavailable")
        assert await service.is_access_allowed(ADMIN_ID) == (True, "admin")
        with pytest.raises(RuntimeError):
            await service.is_private_mode()

    asyncio.run(scenario())
