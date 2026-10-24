from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from enan_ultra.handlers import BotHandlers
from enan_ultra.services.style_profile import (
    PROFILE_KEY,
    StyleProfileStore,
    extract_style_preferences,
)
from enan_ultra.storage.database import Database


def test_style_parser_keeps_only_explicit_allowlisted_preferences() -> None:
    updates = extract_style_preferences(
        "Please answer in Bengali, keep replies concise, use bullet points, "
        "no emojis or stickers."
    )
    assert updates == {
        "style": "concise",
        "language": "bn",
        "response_format": "markdown",
        "emoji_frequency": "minimal",
        "sticker_mode": "off",
    }
    assert extract_style_preferences("Concise answers would be interesting to study") == {}
    assert extract_style_preferences(
        "My name is Amina, my password is secret. Please be concise."
    ) == {}


def test_style_profile_requires_consent_is_user_scoped_and_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "style-profile.sqlite3"
        db = Database(path)
        await db.open()
        try:
            store = StyleProfileStore(db)
            await store.learn_message(
                123, "Please keep replies concise.", private_chat=True, temporary=False,
            )
            assert await store.active_values(123, private_chat=True, temporary=False) == {}

            await store.enable(123)
            await store.learn_message(
                123,
                "Please answer in Bengali, keep replies concise, use bullet points, no emojis or stickers.",
                private_chat=True,
                temporary=False,
            )
            values = await store.active_values(123, private_chat=True, temporary=False)
            assert values == {
                "style": "concise",
                "language": "bn",
                "response_format": "markdown",
                "emoji_frequency": "minimal",
                "sticker_mode": "off",
            }
            serialized = await db.get_preference(123, PROFILE_KEY, "")
            assert "Please answer" not in serialized
            assert set(json.loads(serialized)["values"]) == set(values)

            await store.enable(456)
            await store.learn_message(
                456, "Please answer in English.", private_chat=True, temporary=False,
            )
            assert await store.active_values(456, private_chat=True, temporary=False) == {
                "language": "en",
            }
            assert await store.active_values(123, private_chat=False, temporary=False) == {}
            assert await store.active_values(123, private_chat=True, temporary=True) == {}
            assert await store.learn_message(
                123, "Use more emojis.", private_chat=True, temporary=True,
            ) == {}
            assert await store.learn_message(
                123, "Use more emojis.", private_chat=False, temporary=False,
            ) == {}
        finally:
            await db.close()

        reopened = Database(path)
        await reopened.open()
        try:
            assert await StyleProfileStore(reopened).active_values(
                123, private_chat=True, temporary=False,
            ) == values
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_style_profile_off_delete_and_90_day_expiry(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "style-expiry.sqlite3")
        await db.open()
        try:
            store = StyleProfileStore(db)
            await store.enable(42)
            start = datetime.now(UTC)
            await store.learn_message(
                42, "Please use more emojis.", private_chat=True, temporary=False, now=start,
            )
            assert await store.active_values(42, private_chat=True, temporary=False) == {
                "emoji_frequency": "expressive",
            }
            await store.disable(42)
            assert await store.active_values(42, private_chat=True, temporary=False) == {}
            assert await db.get_preference(42, PROFILE_KEY, "")

            removed = await store.cleanup_expired(now=start + timedelta(days=90, seconds=1))
            assert removed == 1
            assert await db.get_preference(42, PROFILE_KEY, "") == ""
            assert await db.get_preference(42, "style_profile_enabled", "off") == "off"

            await store.enable(42)
            await store.learn_explicit_preference(
                42, "language", "en", private_chat=True,
            )
            await store.delete(42)
            assert await store.active_values(42, private_chat=True, temporary=False) == {}
            assert await db.get_preference(42, PROFILE_KEY, "") == ""
            await db.delete_user_data(42, private_chat_id=42)
            assert await store.status(42) == {"enabled": False, "values": {}, "expires_at": None}
        finally:
            await db.close()

    asyncio.run(scenario())


def test_style_profile_handler_denies_before_mutation_and_requires_private_chat(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "style-handler.sqlite3")
        await db.open()
        try:
            handlers = BotHandlers(SimpleNamespace(), db, None, None, None, None)
            handlers._gate = AsyncMock(return_value=False)
            message = SimpleNamespace(replies=[])

            async def reply_text(text: str) -> None:
                message.replies.append(text)

            message.reply_text = reply_text
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7),
                effective_chat=SimpleNamespace(type="private"),
                effective_message=message,
            )
            context = SimpleNamespace(args=["profile", "on"])
            await handlers.style(update, context)
            assert await handlers.style_profiles.enabled(7) is False
            handlers._gate.assert_awaited_once_with(update)

            handlers._gate = AsyncMock(return_value=True)
            update.effective_chat.type = "group"
            await handlers.style(update, context)
            assert await handlers.style_profiles.enabled(7) is False
            assert "ব্যক্তিগত chat" in message.replies[-1]
        finally:
            await db.close()

    asyncio.run(scenario())
