"""Shared Telegram rich-message formatting and Bot API compatibility helpers."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from typing import Any

from telegram import Message
from telegram.error import BadRequest

logger = logging.getLogger(__name__)
RICH_MESSAGE_LIMIT = 32_000
TELEGRAM_MESSAGE_LIMIT = 3_900


class ResponseFormatter:
    """Create safe, consistent layouts for assistant and support messages."""

    @staticmethod
    def assistant(
        text: str, theme: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], str]:
        body = text.strip() or "দুঃখিত, কোনো উত্তর তৈরি করা যায়নি।"
        label = str(theme.get("label", {}).get("bn", ""))[:80] if theme else ""
        icon = str(theme.get("icon", "✨"))[:8] if theme else "✨"
        heading = f"{icon} **AI RESPONSE · {label}**" if label else "✨ **AI RESPONSE**"
        plain_heading = f"{icon} AI RESPONSE · {label}" if label else "✨ AI RESPONSE"
        return (
            {"markdown": f"{heading}\n\n{body}"},
            f"{plain_heading}\n\n{body}",
        )

    @staticmethod
    def admin_reply(
        text: str, ticket_id: int, sent_at: datetime | None = None,
        theme: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], str]:
        body = text.strip() or "An administrator sent an empty reply."
        timestamp = (sent_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        label = str(theme.get("label", {}).get("bn", ""))[:80] if theme else ""
        icon = str(theme.get("icon", "🛡️"))[:8] if theme else "🛡️"
        heading = f"{icon} **ADMIN RESPONSE · {label}**" if label else "🛡️ **ADMIN RESPONSE**"
        plain_heading = f"{icon} ADMIN RESPONSE · {label}" if label else "🛡️ ADMIN RESPONSE"
        markdown = f"{heading}\n\n🎫 Support request #{ticket_id}\n🕒 {timestamp}\n\n💬\n\n{body}"
        return {"markdown": markdown}, f"{plain_heading}\n\n🎫 Support request #{ticket_id}\n🕒 {timestamp}\n\n💬\n\n{body}"

    @staticmethod
    def support_request(
        user_id: int, display_name: str, snippet: str, ticket_id: int,
        theme: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], str]:
        safe_name = html.escape(display_name[:80], quote=True)
        safe_snippet = html.escape(snippet[:1000], quote=True)
        safe_ticket = str(int(ticket_id))
        label = str(theme.get("label", {}).get("bn", ""))[:80] if theme else ""
        icon = str(theme.get("icon", "📨"))[:8] if theme else "📨"
        themed_heading = f"{icon} SUPPORT REQUEST · {html.escape(label, quote=True)}" if label else "📨 SUPPORT REQUEST"
        rich = (
            f"<h2>{themed_heading}</h2>"
            f"<p><b>👤 User support</b> · #{safe_ticket}</p>"
            f"<p><b>User:</b> {safe_name} · <code>{int(user_id)}</code></p>"
            f"<blockquote>{safe_snippet}</blockquote>"
            "<p>Reply to this message to respond to the user.</p>"
        )
        plain = (
            f"📨 SUPPORT REQUEST #{safe_ticket}\n"
            f"👤 {display_name[:80]} · user {int(user_id)}\n\n"
            f"{snippet[:1000]}\n\nReply to this message to respond to the user."
        )
        return {"html": rich}, plain

    @staticmethod
    def escape_html(value: str) -> str:
        return html.escape(value, quote=True)


def split_telegram_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split plain text by Telegram's UTF-16 message limit without breaking Unicode."""
    chunks: list[str] = []
    current: list[str] = []
    units = 0
    for char in text:
        char_units = 2 if ord(char) > 0xFFFF else 1
        if units + char_units > limit:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += char_units
    if current:
        chunks.append("".join(current))
    return chunks or [""]


class TelegramRichMessageService:
    """Use Bot API 10.1+ rich endpoints with ordinary-message fallbacks.

    python-telegram-bot 22.8 supports Bot API 10.0, so the two rich endpoints
    are sent through PTB's request pipeline until a typed library method exists.
    """

    async def _call(self, bot: Any, endpoint: str, data: dict[str, Any]) -> Any:
        post = getattr(bot, "_post", None)
        if not callable(post):
            raise NotImplementedError("This Telegram library version has no raw Bot API request method")
        return await post(endpoint, data)

    async def send_rich(
        self,
        bot: Any,
        chat_id: int,
        rich_message: dict[str, Any],
        *,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Any:
        data: dict[str, Any] = {"chat_id": chat_id, "rich_message": rich_message}
        if message_thread_id:
            data["message_thread_id"] = message_thread_id
        if reply_to_message_id:
            data["reply_parameters"] = {"message_id": reply_to_message_id}
        result = await self._call(bot, "sendRichMessage", data)
        if isinstance(result, dict):
            return Message.de_json(result, bot)
        return result

    async def send_draft(
        self,
        bot: Any,
        chat_id: int,
        draft_id: int,
        rich_message: dict[str, Any],
        *,
        can_stop: bool,
        message_thread_id: int | None = None,
    ) -> bool:
        data: dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "rich_message": rich_message,
            "can_stop": can_stop,
            "keep_on_stop": True,
        }
        if message_thread_id:
            data["message_thread_id"] = message_thread_id
        try:
            result = await self._call(bot, "sendRichMessageDraft", data)
        except BadRequest:
            if not can_stop:
                raise
            # Bot API 10.1/10.2 support rich drafts but not the 10.3 stop flags.
            data.pop("can_stop", None)
            data.pop("keep_on_stop", None)
            result = await self._call(bot, "sendRichMessageDraft", data)
        return bool(result)

    async def reply_assistant(
        self, message: Any, text: str, theme: dict[str, Any] | None = None,
    ) -> None:
        rich_message, plain_text = ResponseFormatter.assistant(text, theme)
        pieces = split_telegram_text(plain_text)
        if len(text) <= RICH_MESSAGE_LIMIT:
            try:
                await self.send_rich(
                    message.get_bot(),
                    message.chat_id,
                    rich_message,
                    message_thread_id=getattr(message, "message_thread_id", None),
                    reply_to_message_id=message.message_id,
                )
                return
            except (BadRequest, NotImplementedError, AttributeError) as exc:
                logger.info("Rich assistant reply unavailable (%s); using text fallback", type(exc).__name__)
        for index, piece in enumerate(pieces):
            if index:
                import asyncio

                await asyncio.sleep(1.05)
            await message.reply_text(piece)

    async def send_admin_reply(
        self, bot: Any, chat_id: int, text: str, ticket_id: int,
        theme: dict[str, Any] | None = None,
    ) -> None:
        rich_message, plain_text = ResponseFormatter.admin_reply(text, ticket_id, theme=theme)
        if len(plain_text) <= RICH_MESSAGE_LIMIT:
            try:
                await self.send_rich(bot, chat_id, rich_message)
                return
            except (BadRequest, NotImplementedError, AttributeError) as exc:
                logger.info("Rich admin reply unavailable (%s); using text fallback", type(exc).__name__)
        for piece in split_telegram_text(plain_text):
            await bot.send_message(chat_id=chat_id, text=piece)

    async def send_support_request(
        self, bot: Any, chat_id: int, user_id: int, display_name: str, snippet: str, ticket_id: int,
        theme: dict[str, Any] | None = None,
    ) -> Any:
        rich_message, plain_text = ResponseFormatter.support_request(
            user_id, display_name, snippet, ticket_id, theme,
        )
        try:
            return await self.send_rich(bot, chat_id, rich_message)
        except (BadRequest, NotImplementedError, AttributeError) as exc:
            logger.info("Rich support notice unavailable (%s); using text fallback", type(exc).__name__)
            return await bot.send_message(chat_id=chat_id, text=plain_text)
