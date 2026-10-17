from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ApplicationHandlerStop

from enan_ultra.handlers import BotHandlers
from enan_ultra.storage.database import Database


def test_update_id_claim_is_atomic_and_retention_is_bounded(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "state.sqlite3")
        await db.open()
        try:
            claims = await asyncio.gather(*(db.claim_update(778899) for _ in range(24)))
            assert sum(claims) == 1
            assert await db.claim_update(778900)
            assert not await db.claim_update(778900)

            await db._db().execute(
                "UPDATE processed_updates SET processed_at='2000-01-01T00:00:00+00:00' "
                "WHERE update_id=778899",
            )
            await db._db().commit()
            assert await db.cleanup_processed_updates(retention_days=30) == 1
            assert await db.claim_update(778899)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_extension_handler_stops_duplicate_and_database_failure_closed(tmp_path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "state.sqlite3")
        await db.open()
        try:
            handlers = object.__new__(BotHandlers)
            handlers.db = db
            handlers.active_generations = {}
            update = SimpleNamespace(update_id=900, api_kwargs={})

            await handlers.on_extension_update(update, SimpleNamespace())
            with pytest.raises(ApplicationHandlerStop):
                await handlers.on_extension_update(update, SimpleNamespace())

            failed = object.__new__(BotHandlers)

            class FailingDatabase:
                async def claim_update(self, _update_id: int) -> bool:
                    raise RuntimeError("storage unavailable")

            failed.db = FailingDatabase()
            with pytest.raises(ApplicationHandlerStop):
                await failed.on_extension_update(
                    SimpleNamespace(update_id=901, api_kwargs={}), SimpleNamespace(),
                )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_handler_install_is_idempotent_and_rejects_a_second_application() -> None:
    settings = SimpleNamespace()
    handlers = BotHandlers(settings, None, None, None, None, None)

    class FakeApplication:
        def __init__(self) -> None:
            self.handlers = []
            self.bot_data = {}

        def add_handler(self, handler, group=0) -> None:
            self.handlers.append((handler, group))

    first = FakeApplication()
    handlers.install(first)
    handler_count = len(first.handlers)
    handlers.install(first)
    assert len(first.handlers) == handler_count

    with pytest.raises(RuntimeError, match="different Application"):
        handlers.install(FakeApplication())
    with pytest.raises(RuntimeError, match="already been installed"):
        BotHandlers(settings, None, None, None, None, None).install(first)

    update_callbacks = [
        handler for handler, _group in first.handlers
        if getattr(handler, "callback", None) == handlers.update_callback
    ]
    assert len(update_callbacks) == 1


def test_update_authorization_prevents_non_admin_staging() -> None:
    async def scenario() -> None:
        handlers = BotHandlers(SimpleNamespace(admin_id=1234), None, None, None, None, None)
        reply_text = AsyncMock()
        update = SimpleNamespace(
            effective_message=SimpleNamespace(reply_text=reply_text),
            effective_chat=SimpleNamespace(id=555),
            effective_user=SimpleNamespace(id=456),
        )
        await handlers.update(update, SimpleNamespace())
        reply_text.assert_awaited_once()
        assert "ADMIN_ID" in reply_text.await_args.args[0]

    asyncio.run(scenario())


def test_non_admin_cannot_block_or_unblock_users() -> None:
    async def scenario() -> None:
        db = SimpleNamespace(
            is_user_blocked=AsyncMock(return_value=False),
            set_user_blocked=AsyncMock(),
            set_user_unblocked=AsyncMock(),
        )
        settings = SimpleNamespace(
            admin_id=1234, effective_admin_user_ids=frozenset({1234}),
            admin_chat_id=999, access_mode="public", allowed_user_ids=frozenset(),
        )
        handlers = BotHandlers(settings, db, None, None, None, None)
        reply_text = AsyncMock()
        update = SimpleNamespace(
            effective_message=SimpleNamespace(reply_text=reply_text),
            effective_chat=SimpleNamespace(id=555),
            effective_user=SimpleNamespace(id=456),
        )
        await handlers.block_user(update, SimpleNamespace(args=["7001", "abuse"]))
        await handlers.unblock_user(update, SimpleNamespace(args=["7001"]))
        db.set_user_blocked.assert_not_awaited()
        db.set_user_unblocked.assert_not_awaited()
        assert reply_text.await_count == 2
        assert all(
            "authorized administrators" in call.args[0]
            for call in reply_text.await_args_list
        )

    asyncio.run(scenario())

