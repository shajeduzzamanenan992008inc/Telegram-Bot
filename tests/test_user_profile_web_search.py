from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from enan_ultra.handlers import BotHandlers
from enan_ultra.models import AssistantResult, UserProfile
from enan_ultra.services.web_search import parse_search_results
from enan_ultra.storage.database import Database


def test_user_profile_facts_are_saved_updated_and_cleared(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            await db.save_user_fact(123, "Name", "Amina")
            await db.save_user_fact(123, "name", "Amina Rahman")
            await db.save_user_fact(123, "favorite_topic", "astronomy")
            await db.save_user_fact(456, "name", "Other user")
            assert await db.get_user_facts(123) == {
                "name": "Amina Rahman", "favorite_topic": "astronomy",
            }

            columns = await db._db().execute_fetchall("PRAGMA table_info(user_memories)")
            assert {row["name"] for row in columns} == {"user_id", "key", "value"}
            await db.clear_user_conversation(123)
            assert await db.get_user_facts(123) == {}
            assert await db.get_user_facts(456) == {"name": "Other user"}
        finally:
            await db.close()

    asyncio.run(scenario())


def test_learned_knowledge_is_scoped_exported_and_removed_with_user(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            await db.save_learned_knowledge(123, "user_preference", "favorite_food", "burgers")
            await db.save_learned_knowledge(
                0, "general_knowledge", "earth_shape", "Earth is approximately spherical.",
                source_user_id=123,
            )
            await db.save_learned_knowledge(456, "fact", "city", "Dhaka")

            assert await db.get_learned_knowledge(123) == [
                {"category": "user_preference", "key": "favorite_food", "value": "burgers"},
                {
                    "category": "general_knowledge", "key": "earth_shape",
                    "value": "Earth is approximately spherical.",
                },
            ]
            assert await db.get_learned_knowledge(456) == [
                {"category": "fact", "key": "city", "value": "Dhaka"},
                {
                    "category": "general_knowledge", "key": "earth_shape",
                    "value": "Earth is approximately spherical.",
                },
            ]

            exported = await db.export_user_data(123)
            assert len(exported["learned_knowledge"]) == 2
            await db.clear_user_conversation(123)
            assert await db.get_learned_knowledge(123) == []
            assert await db.get_learned_knowledge(456) == [
                {"category": "fact", "key": "city", "value": "Dhaka"},
            ]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_background_fact_extraction_requires_opt_in_and_injects_saved_facts(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            class FakeOpenRouter:
                settings = SimpleNamespace(default_model="openrouter/free")
                calls = 0

                async def complete(self, messages: list[dict], **kwargs: object) -> AssistantResult:
                    self.calls += 1
                    assert kwargs["max_tokens"] == 350
                    return AssistantResult(
                        text='[{"category":"user_preference","key":"favorite_food","value":"burgers"},'
                        '{"category":"general_knowledge","key":"earth_shape",'
                        '"value":"Earth is approximately spherical."}]',
                        model="openrouter/free",
                    )

            openrouter = FakeOpenRouter()
            router = SimpleNamespace(openrouter=openrouter)
            settings = SimpleNamespace(
                memory_enabled_by_default=False, bot_name="Enan",
                persona_profile="warm", media_style="balanced",
            )
            handlers = BotHandlers(settings, db, router, None, None, None)

            await handlers.extract_and_learn_facts(123, "I love burgers.", "Glad to know!")
            assert openrouter.calls == 0

            await db.set_preference(123, "memory_enabled", "on")
            await handlers.extract_and_learn_facts(123, "I love burgers.", "Glad to know!")
            assert openrouter.calls == 0

            await db.set_preference(123, "auto_learning_enabled", "on")
            await handlers.extract_and_learn_facts(123, "I love burgers.", "Glad to know!")
            assert openrouter.calls == 1
            facts = await db.get_learned_knowledge(123)
            assert {fact["key"] for fact in facts} == {"favorite_food", "earth_shape"}
            prompt = handlers._system_prompt(
                UserProfile(), [], "", learned_knowledge=facts,
            )
            assert "Learned knowledge about this user/context" in prompt
            assert '"favorite_food"' in prompt

            await db.set_preference(123, "memory_enabled", "off")
            await handlers.extract_and_learn_facts(123, "Another fact.", "Okay.")
            assert openrouter.calls == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_background_fact_extraction_ignores_invalid_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            class FakeOpenRouter:
                settings = SimpleNamespace(default_model="openrouter/free")

                async def complete(self, _messages: list[dict], **_kwargs: object) -> AssistantResult:
                    return AssistantResult(text="not json", model="openrouter/free")

            handlers = BotHandlers(
                SimpleNamespace(memory_enabled_by_default=False),
                db,
                SimpleNamespace(openrouter=FakeOpenRouter()),
                None, None, None,
            )
            await db.set_preference(123, "memory_enabled", "on")
            await db.set_preference(123, "auto_learning_enabled", "on")
            await handlers.extract_and_learn_facts(123, "My favorite food is soup.", "Thanks.")
            assert await db.get_learned_knowledge(123) == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_explicit_fact_extraction_and_profile_context() -> None:
    assert BotHandlers._extract_explicit_user_facts(
        "My name is Amina. I prefer concise answers."
    ) == [("name", "Amina"), ("response_preference", "concise answers")]
    assert BotHandlers._extract_explicit_user_facts(
        "আমার নাম রাহিম। আমি বাংলা পছন্দ করি।"
    ) == [("name", "রাহিম"), ("likes", "বাংলা")]

    handlers = BotHandlers(
        SimpleNamespace(bot_name="Enan", persona_profile="warm", media_style="balanced"),
        None, None, None, None, None,
    )
    prompt = handlers._system_prompt(
        UserProfile(), [], "", user_facts={"favorite_topic": "astronomy"},
    )
    assert "User profile context" in prompt
    assert '"favorite_topic": "astronomy"' in prompt


def test_web_search_parser_returns_top_three_canonical_sources() -> None:
    html = """
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fnews">First</a>
      <a class="result__snippet">First snippet</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.net/story">Second</a>
      <a class="result__snippet">Second snippet</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.com/third">Third</a>
      <a class="result__snippet">Third snippet</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.com/fourth">Fourth</a>
      <a class="result__snippet">Fourth snippet</a>
    </div>
    """
    results = parse_search_results(html)
    assert len(results) == 3
    assert results[0] == {
        "title": "First", "url": "https://example.org/news", "snippet": "First snippet",
    }
