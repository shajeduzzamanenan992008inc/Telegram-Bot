"""End-to-end chat pipeline with fake Telegram objects and a fake AI provider adapter."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from enan_ultra.actions import ActionRegistry
from enan_ultra.files.registry import FileRegistry
from enan_ultra.handlers import BotHandlers
from enan_ultra.models import AssistantResult
from enan_ultra.services.agents import AgentOrchestrator
from enan_ultra.services.media import MediaProcessor
from enan_ultra.services.mission_control import MissionControl
from enan_ultra.services.providers import ProviderRouter
from enan_ultra.storage.database import Database


class FakeAdapter:
    """A Groq-like adapter whose model is NOT declared tool-capable (the default configuration)."""

    name = "groq"

    def __init__(self):
        self.calls = []

    def configured_model(self):
        return "fake-model"

    def capabilities(self, model=None):
        return {"input": ["text"], "output": ["text"], "tools": False, "streaming": False, "local": False}

    async def list_models(self):
        return ["fake-model"]

    async def health(self):
        return {"status": "healthy"}

    async def close(self):
        return None

    async def complete(self, model, messages, *, tools=None, max_tokens=1400, on_token=None):
        self.calls.append({"messages": messages, "tools": tools})
        return AssistantResult(
            text="Hello from the fake model", model="groq::fake-model",
            usage={"input_tokens": 11, "output_tokens": 6},
        )


def fake_update(text, user_id=42, chat_type="private"):
    user = SimpleNamespace(id=user_id, full_name="Test User", first_name="Test", username="tester",
                           is_bot=False, language_code="en")
    chat = SimpleNamespace(id=user_id if chat_type == "private" else -100123, type=chat_type, title=None)
    bot = MagicMock()
    for name in ("send_chat_action", "send_message", "send_sticker", "send_voice", "send_document",
                 "edit_message_text", "send_message_draft"):
        setattr(bot, name, AsyncMock(return_value=SimpleNamespace(message_id=900)))
    bot._post = AsyncMock(side_effect=NotImplementedError)  # no rich-message API: use plain replies
    replies = []

    async def reply_text(value, **kwargs):
        replies.append(value)
        return SimpleNamespace(message_id=901, chat_id=chat.id, edit_text=AsyncMock(), delete=AsyncMock())

    message = SimpleNamespace(
        text=text, caption=None, photo=None, voice=None, audio=None, video=None, video_note=None, document=None,
        sticker=None, reply_to_message=None, message_id=7, chat=chat, from_user=user, chat_id=chat.id, date=None,
        entities=None, caption_entities=None, reply_text=reply_text, reply_html=reply_text,
        reply_document=AsyncMock(), reply_sticker=AsyncMock(), reply_voice=AsyncMock(), reply_photo=AsyncMock(),
        get_bot=lambda: bot, message_thread_id=None, is_topic_message=False,
    )
    update = SimpleNamespace(effective_message=message, effective_user=user, effective_chat=chat, message=message,
                             callback_query=None, update_id=1)
    context = SimpleNamespace(bot=bot, bot_data={}, user_data={}, chat_data={}, args=[], application=MagicMock(),
                              error=None)
    context.application.create_task = lambda coro, **kwargs: asyncio.ensure_future(coro)
    context.application.bot_data = context.bot_data
    return update, context, replies


def run_pipeline(make_settings, scenario, *, public=True):
    settings = make_settings(
        OPENROUTER_API_KEY=None, GROQ_API_KEY="gsk_test", GROQ_MODEL="fake-model", REQUEST_COOLDOWN_SECONDS="0",
    )

    async def main():
        database = Database(
            settings.database_path, settings.document_retention_hours, settings.max_pending_reminders,
            settings.app_data_key, settings.kb_max_documents, settings.kb_max_bytes,
        )
        await database.open()
        if public:
            await database.set_global_access_mode("public", 1)
        router = ProviderRouter(settings, None)
        for original in list(router.adapters.values()):
            with contextlib.suppress(Exception):  # best-effort cleanup of the replaced real adapters
                await original.close()
        adapter = FakeAdapter()
        router.adapters = {"groq": adapter}
        handlers = BotHandlers(
            settings, database, router, FileRegistry(router), MediaProcessor(settings, router),
            ActionRegistry(settings, home_assistant=None, vm_control=None, sandbox=None),
            AgentOrchestrator(settings, database, router), MissionControl(settings, database, router),
        )
        try:
            return await scenario(handlers, database, adapter)
        finally:
            await database.close()

    return asyncio.run(main())


def test_plain_message_is_answered_and_saved(make_settings):
    async def scenario(handlers, database, adapter):
        update, context, replies = fake_update("tell me a joke about cats")
        await handlers.on_message(update, context)
        assert any("Hello from the fake model" in reply for reply in replies)
        assert len(adapter.calls) == 1 and adapter.calls[0]["tools"] is None
        assert [item["role"] for item in await database.history(42, 10)] == ["user", "assistant"]

    run_pipeline(make_settings, scenario)


def test_greeting_is_answered_even_when_no_model_declares_tool_support(make_settings):
    """Regression: words like 'hello', 'hi' or 'which' made the bot request tools, and the router then
    failed the whole request with 'AI service unavailable' unless AI_TOOL_CAPABLE_MODELS was configured."""

    async def scenario(handlers, database, adapter):
        for text in ("hello", "which one is better?", "hi there"):
            update, context, replies = fake_update(text)
            await handlers.on_message(update, context)
            assert any("Hello from the fake model" in reply for reply in replies), (text, replies)
            assert not any("unavailable" in reply.lower() for reply in replies)
        assert all(call["tools"] is None for call in adapter.calls)

    run_pipeline(make_settings, scenario)


def test_fresh_installation_is_private_and_does_not_call_the_provider(make_settings):
    async def scenario(handlers, database, adapter):
        update, context, replies = fake_update("hello")
        await handlers.on_message(update, context)
        assert replies and "Private Mode" in replies[0] and "**" not in replies[0]
        assert adapter.calls == []

    run_pipeline(make_settings, scenario, public=False)


def test_admin_can_use_the_bot_in_private_mode(make_settings):
    async def scenario(handlers, database, adapter):
        update, context, replies = fake_update("good morning", user_id=1)
        await handlers.on_message(update, context)
        assert any("Hello from the fake model" in reply for reply in replies)

    run_pipeline(make_settings, scenario, public=False)


def test_pptx_upload_reaches_the_model(make_settings):
    import io

    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Q3 Roadmap"
    slide.placeholders[1].text = "Launch Dhaka pilot in March"
    buffer = io.BytesIO()
    deck.save(buffer)
    data = buffer.getvalue()

    async def scenario(handlers, database, adapter):
        update, context, replies = fake_update(None)
        update.effective_message.document = SimpleNamespace(
            file_id="f1", file_name="deck.pptx", file_size=len(data),
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        update.effective_message.caption = "Summarize this file"
        context.bot.get_file = AsyncMock(return_value=SimpleNamespace(
            file_size=len(data), download_as_bytearray=AsyncMock(return_value=bytearray(data)),
        ))
        await handlers.on_message(update, context)
        assert adapter.calls, "the model was never called"
        assert "Q3 Roadmap" in str(adapter.calls[-1]["messages"])

    run_pipeline(make_settings, scenario)
