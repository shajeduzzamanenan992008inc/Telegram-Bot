from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx

from enan_ultra.handlers import BotHandlers
from enan_ultra.models import AssistantResult, UserProfile
from enan_ultra.services.conversation import bound_history
from enan_ultra.storage.database import Database


def test_multilingual_messages_keep_original_text_and_follow_up_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            calls: list[list[dict[str, object]]] = []

            class Router:
                openrouter = None

                def default_model(self) -> str:
                    return "openrouter::configured-model"

                async def estimate_cost(self, *_args: object, **_kwargs: object) -> float:
                    return 0.0

                async def complete(self, messages, *, primary_model, **_kwargs):
                    calls.append(messages)
                    return AssistantResult("Mocked answer", primary_model)

            settings = SimpleNamespace(
                bot_name="Enan", persona_profile="warm", media_style="balanced",
                history_messages=12, max_input_chars=10_000, max_output_chars=4_000,
                max_video_frames=4, max_image_bytes=1_000_000,
                web_search_enabled=False, web_search_cost_reserve_usd=0.0,
                agent_max_workers=1, agent_max_steps=1, agent_max_cost_usd=0.0,
                voice_output_mode="off", tts_cost_reserve_usd=0.0,
                default_model="openrouter::configured-model", ha_base_url="",
                effective_admin_user_ids=frozenset(),
            )
            handlers = BotHandlers(settings, db, Router(), None, None, None)
            class Messages:
                async def send_draft(self, *_args: object, **_kwargs: object) -> None:
                    raise RuntimeError("drafts not available in this test")

            handlers.messages = Messages()
            handlers.theme_engine = SimpleNamespace(
                resolve=lambda *_args, **_kwargs: asyncio.sleep(
                    0, result=SimpleNamespace(mode="off", theme=None),
                ),
            )
            handlers._profile = lambda _user_id, name: asyncio.sleep(
                0, result=UserProfile(
                    name=name, language="auto", memory_enabled=False,
                    media_enabled=False, agents_enabled=False,
                ),
            )
            handlers._request_allowed = lambda *_args, **_kwargs: asyncio.sleep(0, result=1)
            handlers._reply_text = lambda *_args, **_kwargs: asyncio.sleep(0)

            replies: list[str] = []

            async def reply_text(text: str) -> None:
                replies.append(text)

            async def send_chat_action(*_args: object) -> None:
                return None

            async def send_message_draft(**_kwargs: object) -> None:
                raise RuntimeError("drafts not available in this test")

            update_user_id = 71
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=update_user_id, full_name="Tester"),
                effective_chat=SimpleNamespace(id=update_user_id, type="private"),
                effective_message=SimpleNamespace(reply_text=reply_text),
            )
            context = SimpleNamespace(bot=SimpleNamespace(
                send_chat_action=send_chat_action,
                send_message_draft=send_message_draft,
            ))
            inputs = [
                "আমি programming শিখতে চাই",
                "ami programming shikte chai",
                "ami programing sikta cai",
                "amer bot kaj kore na",
                "amar bot kaj kore na",
                "chemistry te atom ki?",
                "আগের উত্তরটা আরও সহজ করে বোলো",
                "explain this in Bangla please",
                "ওটা করো",
            ]
            for prompt in inputs:
                await handlers._chat(update, context, prompt)

            assert [call[-1]["content"] for call in calls] == inputs
            system = calls[0][0]["content"]
            assert isinstance(system, str)
            for instruction in ("Roman Bangla", "typos", "relevant conversation context",
                                 "one short, polite clarification question"):
                assert instruction in system
            follow_up = calls[6]
            assert [message["role"] for message in follow_up[-3:]] == [
                "user", "assistant", "user",
            ]
            assert follow_up[-3]["content"] == "chemistry te atom ki?"
            assert follow_up[-1]["content"] == inputs[6]

            other_user = SimpleNamespace(
                effective_user=SimpleNamespace(id=72, full_name="Other"),
                effective_chat=SimpleNamespace(id=72, type="private"),
                effective_message=SimpleNamespace(reply_text=reply_text),
            )
            await handlers._chat(other_user, context, "hello")
            assert all(message["content"] != inputs[0] for message in calls[-1][1:])
            assert await db.history(72, 10) == [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "Mocked answer"},
            ]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_history_budget_retains_recent_context_and_marks_truncation() -> None:
    history = [
        {"role": "user", "content": "old message"},
        {"role": "assistant", "content": "older answer"},
        {"role": "user", "content": "recent " + "x" * 60},
    ]
    bounded = bound_history(history, 30)
    assert len(bounded) == 1
    assert bounded[0]["role"] == "user"
    assert bounded[0]["content"].startswith("[Earlier context omitted]\n")
    assert len(bounded[0]["content"]) == 30


def test_provider_timeout_is_reported_as_service_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            class Router:
                openrouter = None
                failure = "timeout"

                def default_model(self) -> str:
                    return "openrouter::configured-model"

                async def estimate_cost(self, *_args: object, **_kwargs: object) -> float:
                    return 0.0

                async def complete(self, *_args: object, **_kwargs: object):
                    if self.failure == "empty":
                        return AssistantResult("", "openrouter::configured-model")
                    raise httpx.TimeoutException("provider timed out")

            settings = SimpleNamespace(
                bot_name="Enan", persona_profile="warm", media_style="balanced",
                history_messages=12, max_input_chars=1_000, max_output_chars=4_000,
                max_video_frames=4, max_image_bytes=1_000_000,
                web_search_enabled=False, web_search_cost_reserve_usd=0.0,
                agent_max_workers=1, agent_max_steps=1, agent_max_cost_usd=0.0,
                voice_output_mode="off", tts_cost_reserve_usd=0.0,
                default_model="openrouter::configured-model", ha_base_url="",
                effective_admin_user_ids=frozenset(),
            )
            router = Router()
            handlers = BotHandlers(settings, db, router, None, None, None)
            handlers.theme_engine = SimpleNamespace(
                resolve=lambda *_args, **_kwargs: asyncio.sleep(
                    0, result=SimpleNamespace(mode="off", theme=None),
                ),
            )
            handlers._profile = lambda *_args: asyncio.sleep(
                0, result=UserProfile(memory_enabled=False, media_enabled=False),
            )
            handlers._request_allowed = lambda *_args, **_kwargs: asyncio.sleep(0, result=1)

            replies: list[str] = []

            async def reply_text(text: str) -> None:
                replies.append(text)

            async def send_chat_action(*_args: object) -> None:
                return None

            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=91, full_name="Tester"),
                effective_chat=SimpleNamespace(id=91, type="group"),
                effective_message=SimpleNamespace(reply_text=reply_text),
            )
            context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=send_chat_action))
            await handlers._chat(update, context, "ami programing sikta cai")
            assert replies == ["The AI service is unavailable right now. Please try again later."]
            assert not any("incomprehensible" in item.casefold() for item in replies)

            replies.clear()
            router.failure = "empty"
            await handlers._chat(update, context, "empty response test")
            assert replies == ["The AI service is unavailable right now. Please try again later."]
        finally:
            await db.close()

    asyncio.run(scenario())
