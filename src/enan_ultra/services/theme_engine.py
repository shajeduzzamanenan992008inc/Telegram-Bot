"""Context-aware topic detection and user-scoped adaptive theme selection."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
TOPIC_METADATA_RETENTION_DAYS = 90
_TOPIC_METADATA_KEYS = (
    "theme_last_topic", "theme_last_confidence", "theme_reason_code",
    "theme_candidate_id", "theme_candidate_count", "theme_last_updated_at",
)
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_THEME_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_REQUIRED_COLORS = {"background", "surface", "accent", "glow", "text"}
_SWITCH_CUES = (
    "new topic", "switch topic", "let's talk about", "now let's talk about", "instead",
    "notun topic", "topic change kori", "onno bishoy", "ekhon", "eibar",
    "এখন", "নতুন বিষয়", "বিষয় বদলাই", "এবার", "অন্য প্রসঙ্গ",
)


@dataclass(frozen=True, slots=True)
class TopicDetection:
    topic_id: str
    confidence: float
    keywords: tuple[str, ...]
    reason_code: str
    score: float = 0.0
    matched_count: int = 0


@dataclass(frozen=True, slots=True)
class ThemeDecision:
    topic_id: str
    theme_id: str
    confidence: float
    keywords: tuple[str, ...]
    reason_code: str
    mode: str
    theme: dict[str, Any]


class ThemeRegistry:
    """Load the visual and detector definitions from editable JSON theme files."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[1] / "webapp" / "themes"
        self.themes: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, dict[str, Any]] = {}
        for path in sorted(self.root.glob("*.json")):
            try:
                theme = json.loads(path.read_text(encoding="utf-8"))
                theme_id = str(theme.get("id", ""))
                if not _THEME_ID_RE.fullmatch(theme_id) or theme_id != path.stem:
                    raise ValueError("theme id must match its safe filename")
                label = theme.get("label")
                icon = theme.get("icon")
                animation = theme.get("animation", "aurora")
                if (
                    not isinstance(label, dict)
                    or not any(isinstance(label.get(language), str) for language in ("bn", "en"))
                    or not isinstance(icon, str) or not 1 <= len(icon) <= 16
                    or not isinstance(animation, str) or not _THEME_ID_RE.fullmatch(animation.replace("-", "_"))
                ):
                    raise ValueError("theme needs text labels, a short icon, and a safe animation name")
                colors = theme.get("colors")
                if not isinstance(colors, dict) or not _REQUIRED_COLORS.issubset(colors) or any(
                    not _COLOR_RE.fullmatch(str(value)) for value in colors.values()
                ):
                    raise ValueError("theme colors need accessible six-digit hex values")
                if self._contrast(colors["text"], colors["background"]) < 4.5 or self._contrast(
                    colors["text"], colors["surface"],
                ) < 4.5:
                    raise ValueError("theme text contrast must be at least WCAG AA 4.5:1")
                if min(
                    self._contrast(colors["accent"], colors["background"]),
                    self._contrast(colors["accent"], colors["surface"]),
                ) < 3.0:
                    raise ValueError("theme accent contrast must be at least 3:1")
                detector = theme.get("detector", {})
                if not isinstance(detector, dict):
                    raise ValueError("detector must be an object")
                for key in ("keywords_en", "keywords_bn"):
                    values = detector.get(key, [])
                    if not isinstance(values, list) or len(values) > 200 or any(
                        not isinstance(item, str) or not item.strip() or len(item) > 80 for item in values
                    ):
                        raise ValueError(f"{key} must be a list of non-empty strings")
                loaded[theme_id] = theme
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                logger.warning("Ignoring invalid theme file %s (%s)", path.name, type(exc).__name__)
        if "general" not in loaded:
            raise RuntimeError("The general theme is required as a safe fallback")
        self.themes = loaded

    @staticmethod
    def _contrast(first: str, second: str) -> float:
        def luminance(color: str) -> float:
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    def get(self, theme_id: str | None) -> dict[str, Any]:
        return self.themes.get(theme_id or "", self.themes["general"])

    def client_themes(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item["id"],
                "label": item.get("label", {}),
                "icon": item.get("icon", "✨"),
                "colors": item["colors"],
                "animation": item.get("animation", "aurora"),
                "motion": item.get("motion", "low"),
                "contrast": item.get("contrast", "standard"),
            }
            for item in self.themes.values()
        ]


class TopicDetector:
    """Score current text and recent conversation using a configurable bilingual registry."""

    def __init__(self, registry: ThemeRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _matches(text: str, term: str) -> bool:
        term = term.strip().casefold()
        if not term:
            return False
        if any("\u0980" <= char <= "\u09ff" for char in term):
            return term in text
        head, separator, tail = term.rpartition(" ")
        word = tail if separator else term
        if word.endswith("y"):
            word_pattern = re.escape(word[:-1]) + r"(?:y|ies)"
        elif word.endswith(("s", "x", "ch", "sh")):
            word_pattern = re.escape(word) + r"(?:es)?"
        else:
            word_pattern = re.escape(word) + r"s?"
        phrase_pattern = re.escape(head + separator) + word_pattern
        return re.search(rf"(?<![\w]){phrase_pattern}(?![\w])", text) is not None

    def detect(self, text: str, context: list[str] | None = None) -> TopicDetection:
        current = str(text or "")[:6000].casefold()
        context = [str(item or "")[:4000].casefold() for item in (context or [])[-6:]]
        scores: list[tuple[str, float, tuple[str, ...], int]] = []
        for theme_id, theme in self.registry.themes.items():
            if theme_id == "general":
                continue
            detector = theme.get("detector", {})
            en_terms = detector.get("keywords_en", [])
            bn_terms = detector.get("keywords_bn", [])
            matched: list[str] = []
            score = 0.0
            for term in dict.fromkeys([*en_terms, *bn_terms]):
                normalized = term.casefold().strip()
                if not normalized:
                    continue
                if self._matches(current, normalized):
                    matched.append(term)
                    score += 2.0 + min(0.5 * (len(normalized.split()) - 1), 1.0)
                else:
                    for index, previous in enumerate(reversed(context)):
                        if self._matches(previous, normalized):
                            matched.append(term)
                            score += 0.75 * (0.6 ** index)
                            break
            if score:
                scores.append((theme_id, score, tuple(matched[:8]), len(matched)))

        scores.sort(key=lambda row: row[1], reverse=True)
        if not scores:
            return TopicDetection("general", 0.0, (), "no_topic_match")
        topic_id, score, matched, matched_count = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else 0.0
        confidence = score / (score + second_score + 1.5)
        has_multiword_phrase = any(" " in term.strip() for term in matched)
        if matched_count < 2 and not has_multiword_phrase:
            confidence = min(confidence, 0.49)
        return TopicDetection(topic_id, round(confidence, 3), matched, "keyword_context", score, matched_count)


class AdaptiveThemeEngine:
    """Resolve a topic to a theme while honoring manual mode and switch hysteresis."""

    def __init__(self, registry: ThemeRegistry | None = None) -> None:
        self.registry = registry or ThemeRegistry()
        self.detector = TopicDetector(self.registry)

    async def resolve(
        self, db: Any, user_id: int, text: str = "", context: list[dict[str, Any]] | None = None,
        *, persist: bool = True,
    ) -> ThemeDecision:
        await self._expire_user_metadata(db, user_id)
        tracking = await db.get_preference(user_id, "theme_tracking", "on")
        if tracking == "off":
            current = await self.current(db, user_id)
            return replace(
                current,
                topic_id="general" if current.mode == "auto" else current.topic_id,
                confidence=0.0 if current.mode == "auto" else current.confidence,
                keywords=(),
                reason_code="topic_tracking_paused" if current.mode == "auto" else current.reason_code,
            )
        mode = await db.get_preference(user_id, "theme_mode", "auto")
        if mode not in {"auto", "manual", "off"}:
            mode = "auto"
        if mode == "off":
            selected = "general"
            detection = TopicDetection("general", 0, (), "theme_disabled")
        elif mode == "manual":
            selected = await db.get_preference(user_id, "theme_manual_id", "general")
            if selected not in self.registry.themes:
                selected = "general"
            detection = TopicDetection(selected, 1.0, (), "manual_selection")
        else:
            recent = [
                str(item.get("content", "")) for item in (context or [])[-6:]
            ]
            detection = self.detector.detect(text, recent)
            previous = await db.get_preference(user_id, "theme_current_id", "general")
            if previous not in self.registry.themes:
                previous = "general"
            selected = (
                await self._apply_hysteresis(db, user_id, detection, previous, text)
                if persist else
                detection.topic_id if detection.confidence >= 0.55 else previous
            )
            if selected == "general" and detection.topic_id == "general":
                learning_enabled = await db.get_preference(user_id, "theme_learning", "on") == "on"
                preferred = (
                    await db.get_preference(user_id, "theme_preferred_id", "")
                    if learning_enabled else ""
                )
                if preferred in self.registry.themes:
                    selected = preferred
                    detection = replace(detection, reason_code="preferred_visual_fallback")
        theme = self.registry.get(selected)
        if persist:
            await db.set_preference(user_id, "theme_current_id", selected)
            await db.set_preference(user_id, "theme_last_topic", detection.topic_id)
            await db.set_preference(user_id, "theme_last_confidence", f"{detection.confidence:.3f}")
            await db.set_preference(user_id, "theme_reason_code", detection.reason_code)
            await db.set_preference(user_id, "theme_last_updated_at", datetime.now(UTC).isoformat())
        return ThemeDecision(
            topic_id=detection.topic_id,
            theme_id=selected,
            confidence=detection.confidence,
            keywords=detection.keywords,
            reason_code=detection.reason_code,
            mode=mode,
            theme=theme,
        )

    async def current(self, db: Any, user_id: int) -> ThemeDecision:
        await self._expire_user_metadata(db, user_id)
        mode = await db.get_preference(user_id, "theme_mode", "auto")
        if mode not in {"auto", "manual", "off"}:
            mode = "auto"
        selected = "general"
        reason = "theme_disabled" if mode == "off" else "stored_current_theme"
        if mode == "manual":
            selected = await db.get_preference(user_id, "theme_manual_id", "general")
        elif mode == "auto":
            selected = await db.get_preference(user_id, "theme_current_id", "general")
        if selected not in self.registry.themes:
            selected = "general"
        topic_id = await db.get_preference(user_id, "theme_last_topic", selected)
        confidence = float(await db.get_preference(user_id, "theme_last_confidence", "0") or 0)
        return ThemeDecision(
            topic_id=topic_id,
            theme_id=selected,
            confidence=confidence,
            keywords=(),
            reason_code=reason,
            mode=mode,
            theme=self.registry.get(selected),
        )

    async def set_mode(self, db: Any, user_id: int, mode: str, theme_id: str | None = None) -> ThemeDecision:
        if mode not in {"auto", "manual", "off"}:
            raise ValueError("Theme mode must be auto, manual, or off")
        if mode == "manual":
            if theme_id not in self.registry.themes:
                raise ValueError("Unknown theme")
            await db.set_preference(user_id, "theme_manual_id", str(theme_id))
            await db.set_preference(user_id, "theme_current_id", str(theme_id))
        await db.set_preference(user_id, "theme_mode", mode)
        await db.set_preference(user_id, "theme_candidate_id", "")
        await db.set_preference(user_id, "theme_candidate_count", "0")
        return await self.current(db, user_id)

    async def set_tracking(self, db: Any, user_id: int, enabled: bool) -> None:
        """Control automatic topic inference and erase only its derived metadata when paused."""
        await db.set_preference(user_id, "theme_tracking", "on" if enabled else "off")
        if enabled:
            return
        mode = await db.get_preference(user_id, "theme_mode", "auto")
        keys = (
            "theme_last_topic", "theme_last_confidence", "theme_reason_code",
            "theme_candidate_id", "theme_candidate_count", "theme_last_updated_at",
        )
        if mode != "manual":
            keys = (*keys, "theme_current_id")
        await db.delete_preferences(user_id, keys=keys)

    async def cleanup_expired(
        self, db: Any, *, now: datetime | None = None,
    ) -> int:
        """Expire derived topic metadata while retaining manual appearance preferences."""
        cursor = await db._db().execute(
            "SELECT DISTINCT user_id FROM user_preferences "
            "WHERE key IN ('theme_last_topic','theme_last_confidence','theme_reason_code',"
            "'theme_candidate_id','theme_candidate_count','theme_last_updated_at')",
        )
        rows = await cursor.fetchall()
        await cursor.close()
        instant = now or datetime.now(UTC)
        expired = 0
        for row in rows:
            if await self._expire_user_metadata(db, int(row["user_id"]), now=instant):
                expired += 1
        return expired

    async def _expire_user_metadata(
        self, db: Any, user_id: int, *, now: datetime | None = None,
    ) -> bool:
        raw_updated_at = await db.get_preference(user_id, "theme_last_updated_at", "")
        if not raw_updated_at:
            expired = False
            for key in _TOPIC_METADATA_KEYS[:-1]:
                if await db.get_preference(user_id, key, ""):
                    expired = True
                    break
        else:
            try:
                updated_at = datetime.fromisoformat(raw_updated_at)
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
                expired = updated_at + timedelta(days=TOPIC_METADATA_RETENTION_DAYS) <= (
                    now or datetime.now(UTC)
                )
            except ValueError:
                expired = True
        if not expired:
            return False
        keys = _TOPIC_METADATA_KEYS
        if await db.get_preference(user_id, "theme_mode", "auto") != "manual":
            keys = (*keys, "theme_current_id")
        await db.delete_preferences(user_id, keys=keys)
        return True

    async def update_setting(self, db: Any, user_id: int, key: str, value: str) -> None:
        valid = {
            "appearance_mode": {"system", "dark", "light"},
            "theme_animation": {"auto", "full", "reduced", "off"},
            "theme_learning": {"on", "off"},
            "theme_tracking": {"on", "off"},
            "theme_palette": {"default", "teal", "violet", "amber", "rose"},
        }
        if key not in valid or value not in valid[key]:
            raise ValueError("Unsupported theme preference")
        if key == "theme_tracking":
            await self.set_tracking(db, user_id, value == "on")
            return
        await db.set_preference(user_id, key, value)

    async def favorite(self, db: Any, user_id: int, theme_id: str) -> bool:
        if theme_id not in self.registry.themes:
            raise ValueError("Unknown theme")
        if await db.get_preference(user_id, "theme_learning", "on") != "on":
            return False
        key = f"theme_favorite_{theme_id}"
        old = await db.get_preference(user_id, key, "0")
        new_value = "0" if old == "1" else "1"
        await db.set_preference(user_id, key, new_value)
        if new_value == "1":
            await db.set_preference(user_id, "theme_preferred_id", theme_id)
        elif await db.get_preference(user_id, "theme_preferred_id", "") == theme_id:
            await db.set_preference(user_id, "theme_preferred_id", "")
        return new_value == "1"

    async def _apply_hysteresis(
        self, db: Any, user_id: int, detection: TopicDetection, previous: str, current_text: str,
    ) -> str:
        if detection.topic_id == "general" or detection.confidence < 0.55:
            candidate = await db.get_preference(user_id, "theme_candidate_id", "")
            try:
                count = int(await db.get_preference(user_id, "theme_candidate_count", "0"))
            except ValueError:
                count = 0
            count = count + 1 if candidate == "general" else 1
            await db.set_preference(user_id, "theme_candidate_id", "general")
            await db.set_preference(user_id, "theme_candidate_count", str(count))
            return "general" if count >= 3 else previous
        if detection.topic_id == previous:
            await db.set_preference(user_id, "theme_candidate_id", "")
            await db.set_preference(user_id, "theme_candidate_count", "0")
            return previous

        candidate = await db.get_preference(user_id, "theme_candidate_id", "")
        try:
            count = int(await db.get_preference(user_id, "theme_candidate_count", "0"))
        except ValueError:
            count = 0
        count = count + 1 if candidate == detection.topic_id else 1
        await db.set_preference(user_id, "theme_candidate_id", detection.topic_id)
        await db.set_preference(user_id, "theme_candidate_count", str(count))
        text_folded = current_text.casefold()
        explicit_switch = any(cue in text_folded for cue in _SWITCH_CUES)
        has_specific_phrase = any(" " in keyword.strip() for keyword in detection.keywords)
        strong_topic = detection.confidence >= 0.64 and (
            detection.matched_count >= 2 or has_specific_phrase
        )
        if explicit_switch and detection.confidence >= 0.55 or strong_topic or count >= 2:
            return detection.topic_id
        return previous

    async def reset(self, db: Any, user_id: int) -> None:
        await db.delete_preferences(user_id, prefix="theme_")
        await db.delete_preferences(user_id, keys=("appearance_mode",))
