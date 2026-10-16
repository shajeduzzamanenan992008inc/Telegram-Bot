"""Admin-gated sticker collection, pack import, categorization, and inline retrieval."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telegram import InlineQueryResultCachedSticker, Update
from telegram.ext import ContextTypes

from enan_ultra.config import Settings
from enan_ultra.storage.database import Database

logger = logging.getLogger(__name__)

STICKER_CATEGORIES = frozenset({
    "greeting", "happy", "celebration", "thanks", "thinking", "confused",
    "encouragement", "supportive", "programming", "science", "goodbye", "general",
})
_PACK_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_EMOJI_CATEGORY: dict[str, str] = {
    "👋": "greeting", "🙋": "greeting", "😊": "happy", "😄": "happy",
    "😁": "happy", "😍": "happy", "🥰": "happy", "🤩": "happy",
    "🎉": "celebration", "🥳": "celebration", "🏆": "celebration",
    "🙏": "thanks", "💐": "thanks", "🤔": "thinking", "💭": "thinking",
    "😕": "confused", "😵": "confused", "❓": "confused",
    "💪": "encouragement", "🙌": "encouragement", "👏": "encouragement",
    "🤝": "supportive", "🫂": "supportive", "😢": "supportive",
    "💻": "programming", "🧑‍💻": "programming", "🧪": "science",
    "🔬": "science", "🚪": "goodbye",
}


class StickerCategoryService:
    """Suggest broad categories without treating sticker emoji as definitive emotion."""

    @staticmethod
    def suggest(
        emoji: str | None, set_name: str = "", set_title: str = "",
    ) -> str:
        metadata = f"{set_name} {set_title}".casefold().replace("_", " ")
        for category, words in {
            "greeting": ("hello", "welcome", "greet", "salam"),
            "happy": ("happy", "smile", "joy", "cute"),
            "celebration": ("party", "celebrat", "congrat", "birthday"),
            "thanks": ("thank", "gratitude"),
            "thinking": ("think", "hmm"),
            "confused": ("confus", "question"),
            "encouragement": ("motivat", "encourage", "good luck"),
            "supportive": ("support", "comfort", "care"),
            "programming": ("code", "program", "developer", "computer"),
            "science": ("science", "chemistry", "physics", "biology"),
            "goodbye": ("goodbye", "bye", "farewell"),
        }.items():
            if any(re.search(rf"\b{re.escape(word)}\w*\b", metadata) for word in words):
                return category
        return _EMOJI_CATEGORY.get(emoji or "", "general")


class StickerCollectorService:
    """Persist stickers via the application's asynchronous SQLite database."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._import_locks: dict[str, asyncio.Lock] = {}

    async def collection_enabled(self) -> bool:
        return await self.db.get_setting("sticker_collection_enabled") == "on"

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        sticker = message.sticker if message else None
        if (
            sticker is None or user is None
            or chat is None or chat.type != "private"
            or user.id not in self.settings.effective_admin_user_ids
            or not await self.collection_enabled()
        ):
            return
        try:
            inserted = await self.save_sticker(sticker, added_by=user.id, source_type="admin_collect")
            if inserted:
                logger.info("Collected an administrator-submitted sticker for review")
        except Exception as exc:
            logger.error("Could not persist an administrator-submitted sticker (%s)", type(exc).__name__)

    async def save_sticker(
        self, sticker: Any, *, added_by: int = 0, source_type: str = "pack_import",
        set_name: str = "", set_title: str = "",
    ) -> bool:
        emoji = str(getattr(sticker, "emoji", None) or "")
        set_name = set_name or str(getattr(sticker, "set_name", "") or "")
        return await self.db.save_sticker(
            file_id=str(sticker.file_id),
            file_unique_id=getattr(sticker, "file_unique_id", None),
            emoji=emoji,
            set_name=set_name,
            set_title=set_title,
            sticker_type=str(getattr(sticker, "type", None) or "regular"),
            is_animated=bool(getattr(sticker, "is_animated", False)),
            is_video=bool(getattr(sticker, "is_video", False)),
            width=int(getattr(sticker, "width", 0) or 0),
            height=int(getattr(sticker, "height", 0) or 0),
            category=StickerCategoryService.suggest(emoji, set_name, set_title),
            tags=[],
            source_type=source_type,
            added_by=added_by,
            approved=False,
        )

    async def import_sticker_set(
        self, bot: Any, short_name: str, actor_id: int,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, int]:
        name = short_name.strip()
        if not _PACK_NAME.fullmatch(name):
            raise ValueError("Invalid sticker-set short name")
        lock = self._import_locks.setdefault(name.casefold(), asyncio.Lock())
        if lock.locked():
            raise RuntimeError("An import for this sticker set is already running")
        async with lock:
            sticker_set = await bot.get_sticker_set(name=name)
            stickers = tuple(sticker_set.stickers)
            counts = {"discovered": len(stickers), "imported": 0, "skipped": 0, "failed": 0}
            for offset in range(0, len(stickers), 20):
                if cancel_event and cancel_event.is_set():
                    break
                for sticker in stickers[offset:offset + 20]:
                    if cancel_event and cancel_event.is_set():
                        break
                    try:
                        inserted = await self.save_sticker(
                            sticker, added_by=actor_id, source_type="pack_import",
                            set_name=str(getattr(sticker_set, "name", name) or name),
                            set_title=str(getattr(sticker_set, "title", "") or ""),
                        )
                        counts["imported" if inserted else "skipped"] += 1
                    except Exception as exc:
                        counts["failed"] += 1
                        logger.warning("Sticker pack item could not be stored (%s)", type(exc).__name__)
                await asyncio.sleep(0)
            return counts


async def inline_sticker_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    inline_query = update.inline_query
    if inline_query is None:
        return
    db = context.application.bot_data.get("database")
    if not isinstance(db, Database):
        logger.error("Inline sticker query received before database initialization")
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    try:
        stickers = await db.search_stickers(
            inline_query.query, approved_only=True, limit=20,
        )
        results = [
            InlineQueryResultCachedSticker(
                id=f"sticker-{row['id']}",
                sticker_file_id=str(row["file_id"]),
            )
            for row in stickers
        ]
        await inline_query.answer(results, cache_time=30, is_personal=True)
    except Exception as exc:
        logger.warning("Inline sticker search failed (%s)", type(exc).__name__)
        await inline_query.answer([], cache_time=1, is_personal=True)
