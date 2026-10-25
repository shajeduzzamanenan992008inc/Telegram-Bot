from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from enan_ultra.handlers import BotHandlers
from enan_ultra.storage.database import Database

ADMIN_ID = 1234
SECOND_ADMIN_ID = 5678
ADMIN_CHAT = 9876


def test_update_upload_gate_defaults_disabled_and_expires(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "state.sqlite3")
        await database.open()
        try:
            assert (await database.get_update_upload_gate(100))["state"] == "disabled"
            assert await database.open_update_upload_gate(ADMIN_ID, 100, 30) == "open"
            status = await database.get_update_upload_gate(101)
            assert status == {
                "state": "open", "opened_by": ADMIN_ID, "expires_at": 130.0,
            }
            assert (await database.get_update_upload_gate(130))["state"] == "disabled"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_concurrent_upload_claims_across_database_instances_are_atomic(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "state.sqlite3"
        first = Database(database_path)
        second = Database(database_path)
        await first.open()
        await second.open()
        try:
            await first.open_update_upload_gate(ADMIN_ID, 100, 300)
            claims = await asyncio.gather(*(
                (first if index % 2 else second).claim_update_upload_gate(
                    f"chat:{index}", 101,
                )
                for index in range(20)
            ))
            successful = [token for token, _state in claims if token]
            assert len(successful) == 1
            assert (await first.get_update_upload_gate(102))["state"] == "processing"
            assert await first.open_update_upload_gate(ADMIN_ID, 102, 300) == "processing"
            assert await first.close_update_upload_gate(102) == "processing"
            assert not await second.finish_update_upload_gate("wrong-token")
            assert await second.finish_update_upload_gate(successful[0])
            assert (await first.get_update_upload_gate(103))["state"] == "disabled"
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_restart_recovery_closes_interrupted_processing_and_expired_window(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "state.sqlite3"
        first = Database(database_path)
        await first.open()
        await first.open_update_upload_gate(ADMIN_ID, 100, 30)
        token, state = await first.claim_update_upload_gate("chat:1", 101)
        assert token and state == "processing"
        await first.close()

        restarted = Database(database_path)
        await restarted.open()
        try:
            assert await restarted.recover_update_upload_gate(102) == 1
            assert (await restarted.get_update_upload_gate(102))["state"] == "disabled"
            await restarted.open_update_upload_gate(ADMIN_ID, 200, 30)
            assert await restarted.recover_update_upload_gate(230) == 1
            assert (await restarted.get_update_upload_gate(230))["state"] == "disabled"
        finally:
            await restarted.close()

    asyncio.run(scenario())


def test_update_commands_are_allowlist_scoped_and_upload_enable_is_not_deployment(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "state.sqlite3")
        await database.open()
        try:
            handler = BotHandlers.__new__(BotHandlers)
            handler.settings = SimpleNamespace(
                admin_id=ADMIN_ID,
                effective_admin_user_ids=frozenset({ADMIN_ID, SECOND_ADMIN_ID}),
                admin_chat_id=ADMIN_CHAT,
                update_upload_window_seconds=60,
                update_deployment_enabled=False,
            )
            handler.update_manager = SimpleNamespace(
                admin_ids=frozenset({ADMIN_ID, SECOND_ADMIN_ID}),
                cancel_upload=lambda *_args: False,
            )
            handler.db = database

            async def invoke(user_id: int, args: list[str]) -> AsyncMock:
                reply = AsyncMock()
                update = SimpleNamespace(
                    effective_message=SimpleNamespace(reply_text=reply),
                    effective_chat=SimpleNamespace(id=ADMIN_CHAT),
                    effective_user=SimpleNamespace(id=user_id),
                )
                await handler.update(update, SimpleNamespace(args=args))
                return reply

            unauthorized = await invoke(9999, ["enable"])
            assert "configured Telegram administrators" in unauthorized.await_args.args[0]
            assert (await database.get_update_upload_gate(100))["state"] == "disabled"

            enabled = await invoke(SECOND_ADMIN_ID, ["enable"])
            assert "60 সেকেন্ড" in enabled.await_args.args[0]
            assert "production approval" in enabled.await_args.args[0]
            assert (await database.get_update_upload_gate(100))["state"] == "open"
            assert handler.settings.update_deployment_enabled is False

            status = await invoke(ADMIN_ID, ["status"])
            assert "খোলা" in status.await_args.args[0]
            cancelled = await invoke(ADMIN_ID, ["cancel"])
            assert "বন্ধ" in cancelled.await_args.args[0]
            assert (await database.get_update_upload_gate(100))["state"] == "disabled"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_upload_is_rejected_when_gate_closed_or_sender_is_unauthorized(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "state.sqlite3")
        await database.open()
        try:
            handler = BotHandlers.__new__(BotHandlers)
            handler.settings = SimpleNamespace(
                admin_id=ADMIN_ID,
                effective_admin_user_ids=frozenset({ADMIN_ID, SECOND_ADMIN_ID}),
                admin_chat_id=ADMIN_CHAT,
            )
            handler.update_manager = SimpleNamespace(
                admin_ids=frozenset({ADMIN_ID, SECOND_ADMIN_ID}),
            )
            handler.db = database

            async def submit(user_id: int, message_id: int) -> tuple[AsyncMock, AsyncMock]:
                reply = AsyncMock()
                get_file = AsyncMock()
                handler._update_lock = asyncio.Lock()
                update = SimpleNamespace(
                    effective_message=SimpleNamespace(
                        document=SimpleNamespace(
                            file_name="release.zip", file_size=100, file_id="file-id",
                        ),
                        message_id=message_id,
                        reply_text=reply,
                    ),
                    effective_chat=SimpleNamespace(id=ADMIN_CHAT),
                    effective_user=SimpleNamespace(id=user_id),
                )
                await handler._receive_update_zip(
                    update, SimpleNamespace(bot=SimpleNamespace(get_file=get_file)),
                )
                return reply, get_file

            closed_reply, closed_get_file = await submit(ADMIN_ID, 10)
            assert "ZIP upload বন্ধ" in closed_reply.await_args.args[0]
            closed_get_file.assert_not_awaited()

            await database.open_update_upload_gate(ADMIN_ID, 100, 300)
            unauthorized_reply, unauthorized_get_file = await submit(9999, 11)
            assert "configured Telegram administrators" in unauthorized_reply.await_args.args[0]
            unauthorized_get_file.assert_not_awaited()
            assert (await database.get_update_upload_gate(101))["state"] == "open"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_invalid_upload_closes_the_claimed_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "state.sqlite3")
        await database.open()
        try:
            await database.open_update_upload_gate(ADMIN_ID, time.time(), 300)
            handler = BotHandlers.__new__(BotHandlers)
            handler.settings = SimpleNamespace(
                admin_id=ADMIN_ID,
                effective_admin_user_ids=frozenset({ADMIN_ID}),
                admin_chat_id=ADMIN_CHAT,
                update_upload_window_seconds=300,
                max_upload_bytes=2_000_000,
            )
            handler.update_manager = SimpleNamespace(
                admin_ids=frozenset({ADMIN_ID}),
                start_upload=lambda *_args, **_kwargs: None,
            )
            handler.db = database
            handler._update_lock = asyncio.Lock()
            reply = AsyncMock()
            update = SimpleNamespace(
                effective_message=SimpleNamespace(
                    document=SimpleNamespace(
                        file_name="not-an-update.txt", file_size=10, file_id="file-id",
                    ),
                    message_id=12,
                    reply_text=reply,
                ),
                effective_chat=SimpleNamespace(id=ADMIN_CHAT),
                effective_user=SimpleNamespace(id=ADMIN_ID),
            )
            await handler._receive_update_zip(update, SimpleNamespace())
            assert "staging failed" in reply.await_args.args[0]
            assert (await database.get_update_upload_gate(101))["state"] == "disabled"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_deployment_report_contains_only_digest_and_backup_identifier(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        from enan_ultra.handlers import BotHandlers

        handler = BotHandlers.__new__(BotHandlers)
        handler.update_manager = SimpleNamespace(
            deployment_enabled=True, deployment_socket_path=Path("unused.sock"),
        )
        handler.settings = SimpleNamespace(admin_chat_id=ADMIN_CHAT)
        handler.db = SimpleNamespace(audit=AsyncMock())
        result = {
            "update_id": "a1b2c3d4",
            "state": "rolled_back",
            "manifest_hash": "a" * 64,
            "archive_hash": "b" * 64,
            "backup_id": "20261024T120000Z-a1b2c3d4",
            "validation": {"syntax": "PASS", "tests": "PASS"},
            "failure_code": "health_check_failed",
            "snapshot": "SHOULD_NOT_BE_EXPOSED",
        }

        class Client:
            def __init__(self, _path) -> None:
                pass

            async def recent_results(self):
                return [result]

            async def acknowledge(self, _update_id: str) -> bool:
                return True

        monkeypatch.setattr("enan_ultra.handlers.TelegramDeploymentClient", Client)
        send_message = AsyncMock()
        await handler.notify_deployment_results(
            SimpleNamespace(bot=SimpleNamespace(send_message=send_message)),
        )
        report = send_message.await_args.kwargs["text"]
        assert "Archive SHA-256: " + "b" * 64 in report
        assert "Backup ID: 20261024T120000Z-a1b2c3d4" in report
        assert "syntax PASS, isolated tests PASS" in report
        assert "verified rollback completed" in report
        assert "SHOULD_NOT_BE_EXPOSED" not in report

    asyncio.run(scenario())
