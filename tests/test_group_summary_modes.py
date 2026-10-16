from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from enan_ultra.handlers import BotHandlers
from enan_ultra.models import UserProfile
from enan_ultra.services.web_reader import (
    _validate_public_host,
    _validate_url,
    extract_html_text,
)
from enan_ultra.storage.database import Database


def test_group_message_window_and_per_chat_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            for index in range(110):
                await db.save_group_message(-1001, 55, "Member", f"message-{index}")
            recent = await db.recent_group_messages(-1001, 100)
            assert len(recent) == 80
            assert recent[0]["content"] == "message-30"
            assert recent[-1]["content"] == "message-109"

            await db.set_chat_mode(-1001, "professional")
            await db.set_chat_mode(-1002, "anime")
            assert await db.get_chat_mode(-1001) == "professional"
            assert await db.get_chat_mode(-1002) == "anime"
            assert await db.get_chat_mode(-1003) == "default"
        finally:
            await db.close()

    asyncio.run(scenario())


def test_selected_chat_mode_is_injected_into_system_prompt() -> None:
    handlers = BotHandlers(
        SimpleNamespace(bot_name="Enan", persona_profile="warm", media_style="balanced"),
        None, None, None, None, None,
    )
    prompt = handlers._system_prompt(UserProfile(), [], "", "professional")
    assert "current chat mode is professional" in prompt
    assert "workplace-appropriate language" in prompt
    assert "current chat mode" not in handlers._system_prompt(UserProfile(), [], "", "unknown")


def test_reply_command_creates_personal_task_from_message(tmp_path: Path) -> None:
    class Message:
        def __init__(self) -> None:
            self.reply_to_message = SimpleNamespace(
                text="Prepare the deployment checklist", caption=None,
            )
            self.replies: list[str] = []

        async def reply_text(self, text: str) -> None:
            self.replies.append(text)

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            handlers = BotHandlers(SimpleNamespace(), db, None, None, None, None)

            async def allowed(*_args: object, **_kwargs: object) -> bool:
                return True

            handlers._gate = allowed
            message = Message()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=45),
                effective_chat=SimpleNamespace(id=45, type="private"),
                effective_message=message,
            )
            await handlers.tasks(update, SimpleNamespace(args=["add"]))
            task = (await db.list_tasks(45))[0]
            assert task["task_text"] == "Prepare the deployment checklist"
            assert message.replies[-1] == f"Task #{task['id']} added."
        finally:
            await db.close()

    asyncio.run(scenario())


def test_task_plan_uses_local_privacy_and_persists_dependencies(tmp_path: Path) -> None:
    from enan_ultra.models import AssistantResult

    class Router:
        local_only: bool | None = None
        messages: list[dict[str, object]] = []

        def default_model(self) -> str:
            return "ollama::test-model"

        async def estimate_cost(self, *_args: object, **_kwargs: object) -> float:
            return 0.0

        async def complete(self, _messages, *, primary_model, local_only, **_kwargs):
            self.local_only = local_only
            self.messages.append(_messages)
            return AssistantResult(
                '{"steps":['
                '{"text":"Define acceptance criteria","priority":"high","due_at":null,"depends_on":[]},'
                '{"text":"Implement the change","priority":"normal","due_at":null,"depends_on":[1]}'
                ']}',
                primary_model,
                {"input_tokens": 20, "output_tokens": 25, "cost": 0},
            )

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str) -> None:
            self.replies.append(text)

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3")
        await db.open()
        try:
            router = Router()
            settings = SimpleNamespace(
                cooldown_seconds=0, user_daily_request_limit=10, max_daily_cost_usd=1.0,
                user_monthly_request_limit=30, user_monthly_cost_limit_usd=5.0,
                admin_chat_id=-100, effective_admin_user_ids=frozenset(),
                access_mode="public", allowed_user_ids=frozenset(), history_messages=12,
            )
            handlers = BotHandlers(settings, db, router, None, None, None)
            handlers._profile = lambda *_args: asyncio.sleep(
                0, result=SimpleNamespace(local_only=True),
            )
            await db.save_message(45, "user", "Please help me launch my newsletter")
            message = Message()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=45, full_name="Planner"),
                effective_chat=SimpleNamespace(id=45, type="private"),
                effective_message=message,
            )
            await handlers.tasks(update, SimpleNamespace(args=["plan", "chat"]))
            tasks = await db.list_tasks(45)
            assert len(tasks) == 2
            assert tasks[0]["priority"] == "high"
            dependent = next(task for task in tasks if task["task_text"] == "Implement the change")
            assert dependent["blocked_count"] == 1
            assert router.local_only is True
            assert "launch my newsletter" in router.messages[0][-1]["content"]
            assert "task plan তৈরি হয়েছে" in message.replies[-1]
            usage = await db.usage_today(45)
            assert usage["requests"] == 1

            await db.set_preference(45, "temporary_chat", "on")
            temporary_message = Message()
            temporary_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=45, full_name="Planner"),
                effective_chat=SimpleNamespace(id=45, type="private"),
                effective_message=temporary_message,
            )
            await handlers.tasks(temporary_update, SimpleNamespace(args=["plan", "chat"]))
            assert "Temporary chat" in temporary_message.replies[-1]
            assert len(router.messages) == 1
            await db.set_preference(45, "temporary_chat", "off")

            empty_message = Message()
            empty_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=46, full_name="No history"),
                effective_chat=SimpleNamespace(id=46, type="private"),
                effective_message=empty_message,
            )
            await handlers.tasks(empty_update, SimpleNamespace(args=["plan", "chat"]))
            assert "সংরক্ষিত conversation নেই" in empty_message.replies[-1]
            assert await db.list_tasks(46) == []
            assert len(router.messages) == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_web_reader_extraction_strips_chrome_and_limits_input() -> None:
    extracted = extract_html_text(
        "<header>header junk</header><main><p>Useful text</p>"
        "<script>ignore()</script><footer>footer junk</footer></main>"
    )
    assert extracted == "Useful text"
    assert len(extract_html_text("<main>" + ("x " * 4000) + "</main>")) <= 3000


def test_web_reader_rejects_non_https_and_private_hosts() -> None:
    for url in ("http://example.com", "https://user:pass@example.com", "https://example.com:8443"):
        with pytest.raises(ValueError):
            _validate_url(url)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="non-public"):
            await _validate_public_host("localhost")

    asyncio.run(scenario())
