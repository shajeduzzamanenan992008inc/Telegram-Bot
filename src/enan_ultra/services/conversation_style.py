"""Local, deterministic reply presentation policies."""

from __future__ import annotations

import random
import re
import time
from typing import Any


STYLE_INSTRUCTIONS = {
    "friendly": "Be naturally friendly and warm without canned greetings or unnecessary closing questions.",
    "casual": "Use relaxed, conversational phrasing and concise replies for casual chat; keep technical answers precise.",
    "playful": "Use light, kind humour only when it suits the context; avoid jokes in serious or stressful situations.",
    "professional": "Use respectful, polished, direct language and restrained presentation.",
    "technical": "Be concise and technically precise; preserve exact code, commands, formulas, and identifiers.",
    "tutor": "Teach patiently in clear steps with a simple example when useful; do not overwhelm the learner.",
    "supportive": "Respond calmly and empathetically to difficulties without minimizing them or making unsupported claims.",
}

_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("greeting", ("hello", "hi", "hey", "good morning", "good evening", "assalamu alaikum", "salam",
                  "হ্যালো", "হাই", "আসসালামু", "সালাম", "কেমন আছ", "kemon acho")),
    ("appreciation", ("thank", "thanks", "ধন্যবাদ", "শুকরিয়া", "shukriya", "thank you")),
    ("celebration", ("congrat", "well done", "great job", "অভিনন্দন", "সফল", "হয়ে গেছে", "হয়ে গেছে",
                     "hoise", "hoyeche", "complete", "fixed it", "solved")),
    ("thinking", ("let me think", "what do you think", "hmm", "ভাবছি", "কী মনে হয়", "কি মনে হয়")),
    ("confused", ("confused", "i don't understand", "i do not understand", "বুঝতে পারছি না",
                  "বিভ্রান্ত")),
    ("encouragement", ("good luck", "you can do it", "keep going", "চেষ্টা চালিয়ে যাও",
                       "চেষ্টা চালিয়ে যাও", "তুমি পারবে")),
    ("happy", ("i'm happy", "i am happy", "খুশি", "ভালো লাগছে", "ভাল লাগছে")),
    ("supportive", ("সমস্যা", "কাজ করছে না", "ভয়", "ভয়", "দুঃখ", "কষ্ট", "চিন্তা", "help me",
                    "not working", "i'm stuck", "i am stuck", "worried", "sad", "problem",
                    "kaj kore na", "kaj kortase na", "kaj kortese na")),
    ("programming", ("program", "code", "debug", "python", "javascript", "api", "কোড", "প্রোগ্রাম",
                      "programming", "bug", "compile")),
    ("science", ("chemistry", "physics", "biology", "atom", "science", "রসায়ন", "রসায়ন",
                 "পদার্থ", "জীববিজ্ঞান", "অণু", "পরমাণু")),
    ("learning", ("explain", "teach", "learn", "কীভাবে", "কিভাবে", "বোঝাও", "বুঝিয়ে", "বুঝিয়ে",
                  "শিখতে", "মানে কি", "what is", "how do i")),
    ("goodbye", ("bye", "goodbye", "see you", "বিদায়", "আবার দেখা হবে", "allah hafez")),
)

_STICKER_CATEGORIES = {
    "greeting", "appreciation", "celebration", "thinking", "learning",
    "friendly_reaction", "encouragement", "programming", "science", "goodbye",
}
_SERIOUS_MARKERS = (
    "security", "malware", "hack", "password", "suicide", "emergency", "medical",
    "diagnosis", "legal", "অ্যান্টিভাইরাস", "নিরাপত্তা", "জরুরি", "আত্মহত্যা",
)
_EMOJI = {
    "greeting": "👋",
    "appreciation": "🙏",
    "celebration": "🎉",
    "happy": "😊",
    "thinking": "🤔",
    "confused": "💭",
    "encouragement": "💪",
    "supportive": "🤝",
    "programming": "💻",
    "science": "🧪",
    "learning": "📘",
    "goodbye": "👋",
    "friendly_reaction": "😊",
}


def classify_conversation(text: str) -> tuple[str, float]:
    folded = text.casefold()
    if any(marker in folded for marker in _SERIOUS_MARKERS):
        return "serious", 0.95
    for category, phrases in _PATTERNS:
        if any(phrase in folded for phrase in phrases):
            return category, 0.9
    return "friendly_reaction", 0.35


def decorate_reply(
    response: str, prompt: str, *, frequency: str = "balanced",
    personality: str = "friendly", response_format: str = "text",
) -> str:
    """Add at most one contextual emoji to a short, non-sensitive plain-text reply."""
    if frequency not in {"minimal", "balanced", "expressive"}:
        frequency = "balanced"
    if (
        frequency == "minimal" or response_format == "json" or not response
        or len(response) > 360 or "```" in response or "http://" in response.casefold()
        or "https://" in response.casefold()
        or re.search(r"[\U0001F000-\U0001FAFF]", response)
        or any(marker in prompt.casefold() for marker in _SERIOUS_MARKERS)
    ):
        return response
    category, confidence = classify_conversation(prompt)
    if category == "serious" or confidence < 0.8:
        return response
    if frequency == "balanced" and personality in {"professional", "technical", "supportive"}:
        return response
    emoji = _EMOJI.get(category)
    if not emoji:
        return response
    return response.rstrip() + " " + emoji


class StickerSelector:
    """Conservative per-user sticker selection with in-process cooldowns."""

    def __init__(self, *, cooldown_seconds: float = 1800.0) -> None:
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._last_sent: dict[int, tuple[str, float]] = {}

    def select(
        self, user_id: int, prompt: str, response: str, mode: str,
        registry: list[dict[str, Any]], *, now: float | None = None,
        exclude_file_ids: set[str] | None = None,
    ) -> dict[str, Any] | None:
        if mode not in {"smart", "frequent"} or not response.strip():
            return None
        if len(response) > 700 or "```" in response or any(
            marker in prompt.casefold() for marker in _SERIOUS_MARKERS
        ):
            return None
        category, confidence = classify_conversation(prompt)
        if category in {"serious", "supportive"}:
            return None
        eligible_registry = [
            item for item in registry
            if item.get("approved", True) and int(item.get("send_failures", 0) or 0) < 3
        ]
        explicit_emojis = {
            str(item.get("emoji")) for item in eligible_registry
            if item.get("emoji") and str(item.get("emoji")) in prompt
        }
        if category == "friendly_reaction" and not explicit_emojis:
            return None
        if confidence < 0.8 and not explicit_emojis:
            return None
        aliases = {"appreciation": "thanks"}
        category = aliases.get(category, category)
        excluded = exclude_file_ids or set()
        candidates = [
            item for item in eligible_registry
            if (
                item.get("category") == category
                or any(
                    category == tag.casefold()
                    for tag in (item.get("tags") if isinstance(item.get("tags"), list) else [])
                    if isinstance(tag, str)
                )
                or (item.get("emoji") and str(item.get("emoji")) in explicit_emojis)
            )
            and isinstance(item.get("file_id"), str)
            and item["file_id"].strip()
            and item["file_id"] not in excluded
        ]
        if not candidates:
            return None
        timestamp = time.monotonic() if now is None else now
        previous = self._last_sent.get(user_id)
        cooldown = self.cooldown_seconds / (3 if mode == "frequent" else 1)
        if previous and (previous[0] in {item["file_id"] for item in candidates}
                         or timestamp - previous[1] < cooldown):
            return None
        return random.choice(candidates)

    def mark_delivered(self, user_id: int, file_id: str, *, now: float | None = None) -> None:
        self._last_sent[user_id] = (
            file_id, time.monotonic() if now is None else now,
        )
