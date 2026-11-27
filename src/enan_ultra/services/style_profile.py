"""Opt-in, locally extracted style preferences with bounded retention."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

PROFILE_KEY = "style_profile_v1"
ENABLED_KEY = "style_profile_enabled"
RETENTION_DAYS = 90

ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "style": frozenset({"natural", "concise", "detailed"}),
    "language": frozenset({"bn", "en", "auto"}),
    "emoji_frequency": frozenset({"minimal", "balanced", "expressive"}),
    "response_format": frozenset({"text", "markdown"}),
    "sticker_mode": frozenset({"off", "smart", "frequent"}),
}

_SENSITIVE_INPUT = re.compile(
    r"\b(?:password|passcode|api[_ -]?key|secret|bearer|access token|private key|"
    r"credit card|bank account|national id|nid|diagnosis|medical record|my name is)\b|"
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+?\d[\d ()-]{7,}\d)|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bআমার নাম\b|\bপাসওয়ার্ড\b",
    re.IGNORECASE,
)


def extract_style_preferences(text: str) -> dict[str, str]:
    """Return only finite, explicit style choices; never return source text."""
    if not text or len(text) > 6000 or _SENSITIVE_INPUT.search(text):
        return {}
    folded = text.casefold()
    updates: dict[str, str] = {}

    if re.search(
        r"\b(?:be|keep|make|write|reply|answer|respond)\b[^.!?\n]{0,50}\b(?:concise|brief|short)\b|"
        r"সংক্ষিপ্ত উত্তর|ছোট করে উত্তর|কম কথায় উত্তর|কম কথায় উত্তর",
        folded,
    ):
        updates["style"] = "concise"
    elif re.search(
        r"\b(?:be|keep|make|write|reply|answer|respond|explain)\b[^.!?\n]{0,50}"
        r"\b(?:detailed|in detail|with detail)\b|বিস্তারিত উত্তর|বিস্তারিতভাবে বল",
        folded,
    ):
        updates["style"] = "detailed"
    elif re.search(
        r"\b(?:use|keep|write|reply in|answer in)\b[^.!?\n]{0,40}\bnatural\b|"
        r"স্বাভাবিকভাবে উত্তর|স্বাভাবিক ভাষায় বল|স্বাভাবিক ভাষায় বল",
        folded,
    ):
        updates["style"] = "natural"

    language_match = re.search(
        r"\b(?:reply|answer|respond|write|speak)\s+(?:to me\s+)?in\s+(bengali|bangla|english)\b|"
        r"\b(?:use|prefer)\s+(bengali|bangla|english)\b|"
        r"(বাংলায়|বাংলায়|ইংরেজিতে)\s*(?:উত্তর|কথা বল)",
        folded,
    )
    if language_match:
        language = next((part for part in language_match.groups() if part), "")
        updates["language"] = "en" if language == "english" or language == "ইংরেজিতে" else "bn"

    if re.search(r"\b(?:no|without|avoid)\s+(?:using\s+)?emojis?\b|ইমোজি\s*(?:দিও না|ব্যবহার কোরো না)", folded):
        updates["emoji_frequency"] = "minimal"
    elif re.search(r"\b(?:use|add)\s+(?:more\s+)?emojis?\b|আরও\s*ইমোজি|ইমোজি\s*ব্যবহার করো", folded):
        updates["emoji_frequency"] = "expressive"

    if re.search(r"\b(?:plain text|no markdown|avoid markdown)\b|সাধারণ টেক্সটে", folded):
        updates["response_format"] = "text"
    elif re.search(r"\b(?:markdown|bullet points|bullets|numbered list)\b|বুলেট পয়েন্ট|বুলেট পয়েন্ট", folded):
        updates["response_format"] = "markdown"

    if re.search(
        r"\b(?:no|without|avoid)\s+(?:using\s+)?(?:stickers?|emojis?\s+or\s+stickers?)\b|"
        r"স্টিকার\s*(?:দিও না|পাঠিও না)",
        folded,
    ):
        updates["sticker_mode"] = "off"
    elif re.search(r"\b(?:send|use)\s+stickers?\s+(?:often|frequently)\b", folded):
        updates["sticker_mode"] = "frequent"
    elif re.search(r"\b(?:send|use)\s+stickers?\s+when appropriate\b|উপযুক্ত হলে স্টিকার", folded):
        updates["sticker_mode"] = "smart"

    return updates


class StyleProfileStore:
    """Persist an allowlisted style profile in existing per-user preferences."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def cleanup_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired profile preferences during the regular local maintenance cycle."""
        instant = now or datetime.now(UTC)
        cursor = await self.db._db().execute(
            "SELECT user_id,value FROM user_preferences WHERE key=?",
            (PROFILE_KEY,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        deleted = 0
        for row in rows:
            try:
                record = json.loads(row["value"])
                expires_at = datetime.fromisoformat(str(record["expires_at"]))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                expires_at = instant
            if expires_at <= instant:
                await self.delete(int(row["user_id"]))
                deleted += 1
        return deleted

    async def enabled(self, user_id: int) -> bool:
        return await self.db.get_preference(user_id, ENABLED_KEY, "off") == "on"

    async def enable(self, user_id: int) -> None:
        await self._record(user_id, now=datetime.now(UTC))
        await self.db.set_preference(user_id, ENABLED_KEY, "on")

    async def disable(self, user_id: int) -> None:
        await self.db.set_preference(user_id, ENABLED_KEY, "off")

    async def delete(self, user_id: int) -> None:
        await self.db.delete_preferences(user_id, keys=(PROFILE_KEY, ENABLED_KEY))

    async def status(self, user_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        record = await self._record(user_id, now=now or datetime.now(UTC))
        is_enabled = await self.enabled(user_id)
        return {
            "enabled": is_enabled,
            "values": dict(record.get("values", {})) if is_enabled else {},
            "expires_at": record.get("expires_at") if is_enabled else None,
        }

    async def active_values(
        self, user_id: int, *, private_chat: bool, temporary: bool,
        now: datetime | None = None,
    ) -> dict[str, str]:
        if not private_chat or temporary or not await self.enabled(user_id):
            return {}
        record = await self._record(user_id, now=now or datetime.now(UTC))
        if not await self.enabled(user_id):
            return {}
        return dict(record.get("values", {}))

    async def learn_message(
        self, user_id: int, text: str, *, private_chat: bool, temporary: bool,
        now: datetime | None = None,
    ) -> dict[str, str]:
        if user_id <= 0 or not private_chat or temporary or not await self.enabled(user_id):
            return {}
        updates = extract_style_preferences(text)
        return await self._merge(user_id, updates, now=now or datetime.now(UTC)) if updates else {}

    async def learn_explicit_preference(
        self, user_id: int, key: str, value: str, *, private_chat: bool,
        now: datetime | None = None,
    ) -> dict[str, str]:
        if not private_chat or key not in ALLOWED_VALUES or value not in ALLOWED_VALUES[key]:
            return {}
        if not await self.enabled(user_id):
            return {}
        return await self._merge(user_id, {key: value}, now=now or datetime.now(UTC))

    async def _merge(
        self, user_id: int, updates: dict[str, str], *, now: datetime,
    ) -> dict[str, str]:
        record = await self._record(user_id, now=now)
        values = dict(record.get("values", {}))
        values.update({
            key: value for key, value in updates.items()
            if key in ALLOWED_VALUES and value in ALLOWED_VALUES[key]
        })
        if not values:
            return {}
        expires_at = now + timedelta(days=RETENTION_DAYS)
        await self.db.set_preference(
            user_id,
            PROFILE_KEY,
            json.dumps({
                "version": 1,
                "values": values,
                "updated_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        return values

    async def _record(self, user_id: int, *, now: datetime) -> dict[str, Any]:
        raw = await self.db.get_preference(user_id, PROFILE_KEY, "")
        try:
            record = json.loads(raw) if raw else {}
            expires_at = datetime.fromisoformat(str(record["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                await self.delete(user_id)
                return {}
            raw_values = record.get("values", {})
            values = {
                key: value for key, value in raw_values.items()
                if key in ALLOWED_VALUES and value in ALLOWED_VALUES[key]
            } if isinstance(raw_values, dict) else {}
            return {"values": values, "expires_at": expires_at.isoformat()}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            if raw:
                await self.db.delete_preferences(user_id, keys=(PROFILE_KEY,))
            return {}
