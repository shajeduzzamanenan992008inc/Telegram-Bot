from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from enan_ultra.services.theme_engine import AdaptiveThemeEngine, ThemeRegistry, TopicDetector
from enan_ultra.storage.database import Database


def test_topic_detector_uses_bangla_and_mixed_language_context() -> None:
    detector = TopicDetector(ThemeRegistry())
    programming = detector.detect("আমার Python code-এ bug আছে, debugging করতে সাহায্য করো")
    chemistry = detector.detect("রাসায়নিক বিক্রিয়ায় পরমাণু আর molecule কীভাবে কাজ করে?")
    astronomy = detector.detect("Let's discuss galaxies, planets, and their orbits")
    assert programming.topic_id == "programming" and programming.confidence >= 0.55
    assert chemistry.topic_id == "chemistry" and chemistry.confidence >= 0.55
    assert astronomy.topic_id == "astronomy" and astronomy.confidence >= 0.55


def test_roman_bangla_topics_and_clear_phrase_switches(tmp_path: Path) -> None:
    detector = TopicDetector(ThemeRegistry())
    assert detector.detect("ami python programming sikhte chai").topic_id == "programming"
    assert detector.detect("ekhon chemistry te atom bujhao").topic_id == "chemistry"
    black_hole = detector.detect("black hole niye bolo")
    assert black_hole.topic_id == "astronomy" and black_hole.confidence >= 0.55

    async def scenario() -> None:
        db = Database(tmp_path / "roman-bangla.sqlite3")
        await db.open()
        try:
            engine = AdaptiveThemeEngine()
            assert (await engine.resolve(db, 9001, "ami python programming sikhte chai")).theme_id == "programming"
            changed = await engine.resolve(db, 9001, "ekhon chemistry te atom bujhao")
            assert changed.theme_id == "chemistry"
            changed_again = await engine.resolve(db, 9001, "black hole niye bolo")
            assert changed_again.theme_id == "astronomy"
            recent = await engine.resolve(
                db, 9001, "eta arektu shohoj kore bolo",
                [{"role": "user", "content": "black hole niye bolo"}],
            )
            assert recent.theme_id == "astronomy"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_single_keyword_is_uncertain_and_recent_context_keeps_a_follow_up() -> None:
    async def scenario() -> None:
        detector = TopicDetector(ThemeRegistry())
        assert detector.detect("Python").confidence < 0.55
        class Preferences:
            def __init__(self):
                self.values = {}

            async def get_preference(self, user_id, key, default):
                return self.values.get((user_id, key), default)

            async def set_preference(self, user_id, key, value):
                self.values[(user_id, key)] = value

        db = Preferences()
        engine = AdaptiveThemeEngine()
        decision = await engine.resolve(
            db, 501, "Why does that happen?",
            [{"role": "user", "content": "Explain quantum physics and particle motion"}],
        )
        assert decision.topic_id == "physics"
        assert decision.theme_id == "physics"

    asyncio.run(scenario())


def test_theme_mode_hysteresis_favorites_and_reset_are_user_scoped(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "theme.sqlite3")
        await db.open()
        try:
            engine = AdaptiveThemeEngine()
            first = await engine.resolve(db, 1001, "আমি Python code debugging করছি")
            assert first.theme_id == "programming"

            switched = await engine.resolve(
                db, 1001, "Now quantum physics: explain particles and gravity",
            )
            assert switched.theme_id == "physics"

            follow_up = await engine.resolve(db, 1001, "এটা কেন ঘটছে?", [
                {"role": "user", "content": "Now quantum physics: explain particles and gravity"},
            ])
            assert follow_up.theme_id == "physics"

            assert await engine.set_mode(db, 1001, "manual", "chemistry")
            manual = await engine.resolve(db, 1001, "Let's discuss astronomy and galaxies")
            assert manual.mode == "manual" and manual.theme_id == "chemistry"
            await engine.set_mode(db, 1001, "auto")
            assert await engine.favorite(db, 1001, "biology") is True
            await engine.update_setting(db, 1001, "theme_learning", "off")
            assert await engine.favorite(db, 1001, "chemistry") is False

            assert await db.get_preference(1002, "theme_mode", "auto") == "auto"
            await engine.reset(db, 1001)
            assert await db.get_preference(1001, "theme_mode", "auto") == "auto"
            assert await db.get_preference(1001, "theme_favorite_biology", "") == ""
            assert await db.get_preference(1001, "theme_learning", "on") == "on"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_theme_registry_can_load_a_custom_topic_without_code_changes(tmp_path: Path) -> None:
    (tmp_path / "general.json").write_text(json.dumps({
        "id": "general", "label": {"bn": "সাধারণ"}, "icon": "✨",
        "colors": {"background": "#101010", "surface": "#202020", "accent": "#abcdef", "glow": "#123456", "text": "#ffffff"},
        "detector": {"keywords_en": [], "keywords_bn": []},
    }), encoding="utf-8")
    (tmp_path / "robotics_club.json").write_text(json.dumps({
        "id": "robotics_club", "label": {"bn": "রোবোটিক্স"}, "icon": "🤖",
        "colors": {"background": "#101010", "surface": "#202020", "accent": "#abcdef", "glow": "#123456", "text": "#ffffff"},
        "detector": {"keywords_en": ["robotics club", "servo motor"], "keywords_bn": ["সার্ভো মোটর"]},
    }), encoding="utf-8")
    registry = ThemeRegistry(tmp_path)
    detection = TopicDetector(registry).detect("Our robotics club is testing a servo motor")
    assert detection.topic_id == "robotics_club"
    assert registry.client_themes()[0]["animation"] == "aurora"


def test_topic_tracking_off_clears_metadata_stops_updates_and_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "theme-tracking.sqlite3"
        db = Database(path)
        await db.open()
        try:
            engine = AdaptiveThemeEngine()
            detected = await engine.resolve(db, 601, "I am programming Python and debugging code")
            assert detected.theme_id == "programming"
            assert await db.get_preference(601, "theme_last_topic", "") == "programming"

            await engine.set_tracking(db, 601, False)
            assert await db.get_preference(601, "theme_tracking", "on") == "off"
            for key in (
                "theme_last_topic", "theme_last_confidence", "theme_reason_code",
                "theme_candidate_id", "theme_candidate_count", "theme_last_updated_at",
                "theme_current_id",
            ):
                assert await db.get_preference(601, key, "") == ""

            paused = await engine.resolve(
                db, 601, "Let's discuss quantum physics and particles",
                [{"role": "user", "content": "I like astronomy and galaxies"}],
            )
            assert paused.theme_id == "general"
            assert paused.reason_code == "topic_tracking_paused"
            assert await db.get_preference(601, "theme_last_topic", "") == ""
        finally:
            await db.close()

        reopened = Database(path)
        await reopened.open()
        try:
            current = await AdaptiveThemeEngine().resolve(
                reopened, 601, "Explain chemistry molecules",
            )
            assert current.theme_id == "general"
            assert current.reason_code == "topic_tracking_paused"
            assert await reopened.get_preference(601, "theme_last_topic", "") == ""
            await AdaptiveThemeEngine().set_tracking(reopened, 601, True)
            resumed = await AdaptiveThemeEngine().resolve(
                reopened, 601, "I am programming Python and debugging code",
            )
            assert resumed.theme_id == "programming"
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_favorite_learning_off_disables_saved_fallback_without_deleting_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "theme-learning.sqlite3")
        await db.open()
        try:
            engine = AdaptiveThemeEngine()
            assert await engine.favorite(db, 602, "biology") is True
            assert await db.get_preference(602, "theme_preferred_id", "") == "biology"
            await engine.update_setting(db, 602, "theme_learning", "off")
            assert await engine.favorite(db, 602, "chemistry") is False
            decision = await engine.resolve(db, 602, "Hello there")
            assert decision.theme_id == "general"
            assert await db.get_preference(602, "theme_tracking", "on") == "on"
            assert await db.get_preference(602, "theme_last_updated_at", "")
            assert await db.get_preference(602, "theme_preferred_id", "") == "biology"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_group_and_temporary_resolutions_do_not_persist_topic_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "theme-non-persistent.sqlite3")
        await db.open()
        try:
            engine = AdaptiveThemeEngine()
            group_result = await engine.resolve(
                db, 603, "I am programming Python and debugging code", persist=False,
            )
            temporary_result = await engine.resolve(
                db, 604, "I am programming Python and debugging code", persist=False,
            )
            assert group_result.theme_id == temporary_result.theme_id == "programming"
            for user_id in (603, 604):
                for key in (
                    "theme_last_topic", "theme_last_confidence", "theme_reason_code",
                    "theme_candidate_id", "theme_candidate_count", "theme_last_updated_at",
                    "theme_current_id",
                ):
                    assert await db.get_preference(user_id, key, "") == ""
        finally:
            await db.close()

    asyncio.run(scenario())


def test_topic_metadata_expires_after_90_days_and_keeps_manual_preferences(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "theme-retention.sqlite3")
        await db.open()
        try:
            engine = AdaptiveThemeEngine()
            start = datetime.now(UTC)
            await engine.resolve(db, 605, "I am programming Python and debugging code")
            await engine.set_mode(db, 605, "manual", "chemistry")
            await db.close()
            db = Database(tmp_path / "theme-retention.sqlite3")
            await db.open()
            removed = await engine.cleanup_expired(
                db, now=start + timedelta(days=90, seconds=1),
            )
            assert removed == 1
            assert await db.get_preference(605, "theme_last_topic", "") == ""
            assert await db.get_preference(605, "theme_last_updated_at", "") == ""
            current = await engine.current(db, 605)
            assert current.mode == "manual"
            assert current.theme_id == "chemistry"

            await db.set_preference(606, "theme_last_topic", "astronomy")
            assert await engine.cleanup_expired(db, now=start) == 1
            assert await db.get_preference(606, "theme_last_topic", "") == ""
        finally:
            await db.close()

    asyncio.run(scenario())
