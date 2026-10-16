from __future__ import annotations

import asyncio
import io
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from enan_ultra import handlers as handlers_module
from enan_ultra.sticker_collector import StickerCategoryService, StickerCollectorService
from enan_ultra.sticker_collector import inline_sticker_query
from enan_ultra.runtime import _migrate_legacy_sticker_catalog
from enan_ultra.services import sticker_image, voice
from enan_ultra.services.conversation_style import StickerSelector, classify_conversation, decorate_reply
from enan_ultra.storage.database import Database


def test_sticker_database_deduplicates_and_tracks_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        try:
            results = await asyncio.gather(*(
                db.save_sticker(
                    file_id=f"file-{index}", file_unique_id="unique-laugh",
                    emoji="😂", category="happy", set_name="party_pack",
                    set_title="Party Pack", sticker_type="regular",
                    width=512, height=512, source_type="pack_import",
                )
                for index in range(32)
            ))
            assert sum(results) == 1
            assert await db.save_sticker(
                file_id="without-unique", file_unique_id=None,
                emoji="", category="general",
            )
            assert not await db.save_sticker(
                file_id="without-unique", file_unique_id=None,
                emoji="", category="general",
            )
            rows = await db.list_stickers()
            assert len(rows) == 2
            assert rows[0]["approved"] is False
            assert rows[1]["set_name"] == "party_pack"
            assert rows[1]["file_unique_id"] == "unique-laugh"
            assert await db.list_stickers(approved_only=True) == []
            assert await db.update_sticker_category(rows[1]["id"], "celebration", ["party"])
            assert len(await db.list_stickers(category="celebration", approved_only=True)) == 1
            assert (await db.sticker_stats())["pending"] == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_sticker_collection_requires_admin_private_chat_and_enabled_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        collector = StickerCollectorService(
            db, SimpleNamespace(effective_admin_user_ids=frozenset({7})),
        )
        try:
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7),
                effective_chat=SimpleNamespace(type="private"),
                effective_message=SimpleNamespace(
                    sticker=SimpleNamespace(
                        file_id="happy-sticker", file_unique_id="unique-happy",
                        emoji="😊", width=512, height=512, type="regular",
                    ),
                ),
            )
            await collector.handle(update, None)  # type: ignore[arg-type]
            assert await db.list_stickers() == []
            await db.set_setting("sticker_collection_enabled", "on")
            await collector.handle(SimpleNamespace(
                effective_user=SimpleNamespace(id=8),
                effective_chat=SimpleNamespace(type="private"),
                effective_message=update.effective_message,
            ), None)  # type: ignore[arg-type]
            await collector.handle(SimpleNamespace(
                effective_user=SimpleNamespace(id=7),
                effective_chat=SimpleNamespace(type="group"),
                effective_message=update.effective_message,
            ), None)  # type: ignore[arg-type]
            await collector.handle(update, None)  # type: ignore[arg-type]
            rows = await db.list_stickers()
            assert len(rows) == 1 and rows[0]["category"] == "happy"
            assert rows[0]["approved"] is False
            assert StickerCategoryService.suggest("❓") == "confused"
            assert StickerCategoryService.suggest("👋") == "greeting"
            assert StickerCategoryService.suggest("👋", "goodbye_pack") == "goodbye"
            assert StickerCategoryService.suggest("🫠") == "general"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_sticker_pack_import_handles_duplicates_and_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        collector = StickerCollectorService(
            db, SimpleNamespace(effective_admin_user_ids=frozenset({7})),
        )
        try:
            first = SimpleNamespace(
                file_id="file-1", file_unique_id="unique-1", emoji="🎉",
                set_name="holiday", type="regular", is_animated=False,
                is_video=False, width=512, height=512,
            )
            second = SimpleNamespace(**{**vars(first), "file_id": "file-2"})
            third = SimpleNamespace(**{**vars(first), "file_id": "file-3", "file_unique_id": "unique-3"})

            class Bot:
                async def get_sticker_set(self, *, name):
                    assert name == "holiday_by_bot"
                    return SimpleNamespace(name=name, title="Holiday", stickers=[first, second, third])

            counts = await collector.import_sticker_set(Bot(), "holiday_by_bot", 7)
            assert counts == {"discovered": 3, "imported": 2, "skipped": 1, "failed": 0}
            assert {row["set_title"] for row in await db.list_stickers()} == {"Holiday"}
            try:
                await collector.import_sticker_set(Bot(), "bad name", 7)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid sticker-set names must be rejected")
            cancel = asyncio.Event()
            cancel.set()
            cancelled = await collector.import_sticker_set(Bot(), "holiday_by_bot", 7, cancel)
            assert cancelled["imported"] == 0 and cancelled["discovered"] == 3
        finally:
            await db.close()

    asyncio.run(scenario())


def test_sticker_pack_import_reports_api_and_per_item_failures(tmp_path: Path) -> None:
    async def scenario() -> None:
        from telegram.error import BadRequest

        db = Database(tmp_path / "assistant.db")
        await db.open()
        collector = StickerCollectorService(
            db, SimpleNamespace(effective_admin_user_ids=frozenset({7})),
        )
        try:
            class BrokenBot:
                async def get_sticker_set(self, *, name):
                    del name
                    raise BadRequest("unknown sticker set")

            try:
                await collector.import_sticker_set(BrokenBot(), "unknown_pack", 7)
            except BadRequest:
                pass
            else:
                raise AssertionError("Telegram Bot API failures must be propagated to the handler")

            stickers = [
                SimpleNamespace(
                    file_id=f"file-{index}", file_unique_id=f"unique-{index}",
                    emoji=None, type="regular", is_animated=False, is_video=False,
                    width=512, height=512, set_name="errors",
                )
                for index in range(2)
            ]

            class PackBot:
                async def get_sticker_set(self, *, name):
                    return SimpleNamespace(name=name, title="Errors", stickers=stickers)

            async def save_with_one_failure(sticker, **kwargs):
                if sticker.file_id == "file-0":
                    raise sqlite3.OperationalError("write failed")
                return await db.save_sticker(
                    file_id=sticker.file_id,
                    file_unique_id=sticker.file_unique_id,
                    category="general", **{
                        key: value for key, value in kwargs.items()
                        if key in {"set_name", "set_title", "source_type", "added_by", "approved"}
                    },
                )

            collector.save_sticker = save_with_one_failure  # type: ignore[method-assign]
            counts = await collector.import_sticker_set(PackBot(), "error_pack", 7)
            assert counts == {"discovered": 2, "imported": 1, "skipped": 0, "failed": 1}
        finally:
            await db.close()

    asyncio.run(scenario())


def test_concurrent_import_of_same_pack_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        collector = StickerCollectorService(
            db, SimpleNamespace(effective_admin_user_ids=frozenset({7})),
        )
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowBot:
            async def get_sticker_set(self, *, name):
                started.set()
                await release.wait()
                return SimpleNamespace(name=name, title=name, stickers=[])

        try:
            first_import = asyncio.create_task(
                collector.import_sticker_set(SlowBot(), "same_pack", 7),
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            try:
                await collector.import_sticker_set(SlowBot(), "same_pack", 8)
            except RuntimeError as exc:
                assert "already running" in str(exc)
            else:
                raise AssertionError("Concurrent import of a sticker pack must be rejected")
            release.set()
            assert (await first_import)["discovered"] == 0
        finally:
            release.set()
            await db.close()

    asyncio.run(scenario())


def test_inline_sticker_search_only_exposes_approved_items(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        try:
            await db.save_sticker(
                file_id="pending-file", file_unique_id="pending-unique",
                emoji="😊", category="happy",
            )
            await db.save_sticker(
                file_id="approved-file", file_unique_id="approved-unique",
                emoji="😊", category="happy", approved=True,
            )

            class InlineQuery:
                query = "happy"
                results = None

                async def answer(self, results, **kwargs):
                    del kwargs
                    self.results = results

            query = InlineQuery()
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"database": db}),
            )
            await inline_sticker_query(SimpleNamespace(inline_query=query), context)
            assert len(query.results) == 1
            assert query.results[0].sticker_file_id == "approved-file"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_contextual_categories_tags_emoji_and_conservative_presentation() -> None:
    selector = StickerSelector(cooldown_seconds=0)
    registry = [
        {"id": 1, "file_id": "tagged-programming", "category": "general", "emoji": "💻", "tags": ["programming"]},
        {"id": 2, "file_id": "explicit-emoji", "category": "general", "emoji": "🎉", "tags": []},
    ]
    assert classify_conversation("I need programming help")[0] == "programming"
    assert selector.select(1, "I need programming help", "Here is a fix.", "smart", registry)["file_id"] == "tagged-programming"
    assert selector.select(1, "🎉", "Congratulations!", "smart", registry)["file_id"] == "explicit-emoji"
    assert selector.select(1, "security incident: malware found", "We should inspect logs.", "frequent", registry) is None
    assert decorate_reply("```print('hi')```", "hello", frequency="expressive") == "```print('hi')```"


def test_legacy_sticker_catalog_migrates_to_main_database_pending_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        legacy_path = tmp_path / "stickers_store.db"
        with sqlite3.connect(legacy_path) as connection:
            connection.execute(
                "CREATE TABLE auto_stickers (id INTEGER PRIMARY KEY,file_id TEXT,emoji TEXT,category TEXT)",
            )
            connection.execute(
                "INSERT INTO auto_stickers(file_id,emoji,category) VALUES(?,?,?)",
                ("legacy-file", "😂", "laugh"),
            )
        db = Database(tmp_path / "assistant.db")
        await db.open()
        try:
            await _migrate_legacy_sticker_catalog(db, legacy_path)
            await _migrate_legacy_sticker_catalog(db, legacy_path)
            rows = await db.list_stickers()
            assert len(rows) == 1
            assert rows[0]["file_id"] == "legacy-file"
            assert rows[0]["approved"] is False
            assert rows[0]["source_type"] == "legacy_import"
            assert await db.list_stickers(approved_only=True) == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_existing_registry_schema_is_extended_without_losing_rows(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "assistant.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE sticker_registry (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "file_id TEXT NOT NULL,category TEXT NOT NULL,emoji TEXT DEFAULT '',"
                "label TEXT DEFAULT '',added_by INTEGER DEFAULT 0,added_at TEXT NOT NULL)",
            )
            connection.execute(
                "INSERT INTO sticker_registry(file_id,category,emoji,added_at) "
                "VALUES('old-file','general','✨','2025-01-01T00:00:00+00:00')",
            )
        db = Database(path)
        await db.open()
        try:
            rows = await db.list_stickers()
            assert len(rows) == 1
            assert rows[0]["file_id"] == "old-file"
            assert rows[0]["emoji"] == "✨"
            assert rows[0]["approved"] is False
            columns = {
                row["name"] for row in await db._db().execute_fetchall(
                    "PRAGMA table_info(sticker_registry)",
                )
            }
            assert {"file_unique_id", "set_name", "sticker_type", "usage_count"} <= columns
        finally:
            await db.close()

    asyncio.run(scenario())


def test_admin_collection_commands_toggle_setting_and_reject_non_admin(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        try:
            settings = SimpleNamespace(
                effective_admin_user_ids=frozenset({7}), access_mode="public",
                allowed_user_ids=frozenset(), admin_chat_id=-100,
            )
            handlers = handlers_module.BotHandlers(settings, db, None, None, None, None)

            class Message:
                def __init__(self):
                    self.replies: list[str] = []

                async def reply_text(self, text: str):
                    self.replies.append(text)

            admin_message = Message()
            admin_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7),
                effective_chat=SimpleNamespace(id=7, type="private"),
                effective_message=admin_message,
            )
            await handlers.collect_stickers_on(admin_update, SimpleNamespace())
            assert await handlers.sticker_collector.collection_enabled()
            await handlers.collect_stickers_off(admin_update, SimpleNamespace())
            assert not await handlers.sticker_collector.collection_enabled()

            denied_message = Message()
            denied_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=8),
                effective_chat=SimpleNamespace(id=8, type="private"),
                effective_message=denied_message,
            )
            await handlers.collect_stickers_on(denied_update, SimpleNamespace())
            assert not await handlers.sticker_collector.collection_enabled()
            assert any("administrators" in reply for reply in denied_message.replies)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_reply_keeps_text_on_sticker_failure_and_persists_cooldown(tmp_path: Path) -> None:
    async def scenario() -> None:
        from telegram.error import BadRequest

        db = Database(tmp_path / "assistant.db")
        await db.open()
        try:
            await db.save_message(7, "user", "hello")
            for index in range(2):
                await db.save_sticker(
                    file_id=f"greeting-{index}", file_unique_id=f"greeting-unique-{index}",
                    category="greeting", approved=True,
                )
            settings = SimpleNamespace(effective_admin_user_ids=frozenset(), sticker_cooldown_seconds=0)
            handlers = handlers_module.BotHandlers(settings, db, None, None, None, None)
            handlers._profile = AsyncMock(return_value=SimpleNamespace(
                persona="friendly", response_format="text",
            ))
            handlers.messages.reply_assistant = AsyncMock()
            calls = 0

            async def send_sticker(_file_id: str):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise BadRequest("sticker file unavailable")

            message = SimpleNamespace(reply_sticker=send_sticker)
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7, full_name="Admin"),
                effective_chat=SimpleNamespace(id=7, type="private"),
                effective_message=message,
            )
            await handlers._reply_text(update, "Hello!", theme={})
            assert calls == 2
            handlers.messages.reply_assistant.assert_awaited_once()
            assert await db.last_user_sticker_use(7)
            stats = await db.sticker_stats()
            assert stats["usage_count"] == 1
            assert sum(row["send_failures"] for row in await db.list_stickers()) == 1

            await handlers._reply_text(update, "Hello again!", theme={})
            assert calls == 2
            assert handlers.messages.reply_assistant.await_count == 2
        finally:
            await db.close()

    asyncio.run(scenario())


def test_voice_generation_uses_thread_and_returns_cleanup_path(monkeypatch) -> None:
    class FakeTTS:
        def __init__(self, *, text: str, lang: str) -> None:
            assert text == "Hello voice"
            assert lang == "bn"

        def save(self, path: str) -> None:
            Path(path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(voice, "gTTS", FakeTTS)
    path = asyncio.run(voice.generate_voice(" Hello voice ", "bn-BD"))
    try:
        assert path.suffix == ".mp3"
        assert path.read_bytes() == b"fake-mp3"
    finally:
        path.unlink(missing_ok=True)


def test_voice_reply_removes_generated_file_after_sending(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        audio_path = tmp_path / "voice.mp3"
        audio_path.write_bytes(b"voice")
        bot_handlers = object.__new__(handlers_module.BotHandlers)
        bot_handlers.settings = SimpleNamespace(max_upload_bytes=1024)
        bot_handlers.router = SimpleNamespace(openrouter=None)

        async def profile(_user_id: int, _name: str):
            del _user_id, _name
            return SimpleNamespace(local_only=False, language="bn")

        bot_handlers._profile = profile
        sent = False

        async def reply_voice(_voice) -> None:
            del _voice
            nonlocal sent
            assert audio_path.exists()
            sent = True

        async def fake_generate_voice(_text: str, _lang: str):
            del _text, _lang
            return audio_path

        monkeypatch.setattr(handlers_module, "generate_voice", fake_generate_voice)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7, full_name="Test"),
            effective_message=SimpleNamespace(reply_voice=reply_voice),
        )
        await bot_handlers._send_voice(update, "reply")
        assert sent
        assert not audio_path.exists()

    asyncio.run(scenario())


def test_sticker_image_conversion_preserves_aspect_ratio_and_limits() -> None:
    image = Image.new("RGB", (1200, 600), "red")
    source = io.BytesIO()
    image.save(source, format="PNG")
    result = asyncio.run(sticker_image.create_sticker_webp(source.getvalue()))
    with Image.open(io.BytesIO(result)) as sticker:
        assert sticker.format == "WEBP"
        assert sticker.size == (512, 256)
    assert len(result) <= sticker_image.MAX_STICKER_BYTES


def test_sticker_collector_handler_logs_database_failures(tmp_path: Path, caplog) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.db")
        await db.open()
        await db.set_setting("sticker_collection_enabled", "on")
        collector = StickerCollectorService(
            db, SimpleNamespace(effective_admin_user_ids=frozenset({7})),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(type="private"),
            effective_message=SimpleNamespace(
                sticker=SimpleNamespace(file_id="sticker-id", file_unique_id="unique-id", emoji="🔥"),
            ),
        )

        async def fail_save(*args, **kwargs) -> bool:
            del args, kwargs
            raise sqlite3.OperationalError("database unavailable")

        collector.save_sticker = fail_save  # type: ignore[method-assign]
        try:
            await collector.handle(update, None)  # type: ignore[arg-type]
            assert "Could not persist an administrator-submitted sticker" in caplog.text
        finally:
            await db.close()

    asyncio.run(scenario())
