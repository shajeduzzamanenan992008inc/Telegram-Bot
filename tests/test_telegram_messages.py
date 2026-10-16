from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from telegram import Update
from telegram.error import BadRequest

from enan_ultra.handlers import BotHandlers
from enan_ultra.services.document_export import DocumentExportService
from enan_ultra.services.telegram_messages import (
    ResponseFormatter,
    TelegramRichMessageService,
)
from enan_ultra.services.theme_engine import AdaptiveThemeEngine


def test_response_formatter_keeps_rich_content_and_escapes_support_text() -> None:
    assistant, plain = ResponseFormatter.assistant("## সমাধান\n\n`code`")
    assert assistant["markdown"].startswith("✨ **AI RESPONSE**")
    assert "`code`" in assistant["markdown"]
    assert plain.endswith("## সমাধান\n\n`code`")

    admin, _ = ResponseFormatter.admin_reply(
        "Done", 77, datetime(2026, 10, 15, 12, 30, tzinfo=UTC),
    )
    assert "ADMIN RESPONSE" in admin["markdown"]
    assert "Support request #77" in admin["markdown"]
    assert "2026-10-15 12:30 UTC" in admin["markdown"]

    support, _ = ResponseFormatter.support_request(9, "<img onerror=x>", "<tg-button>", 12)
    assert "&lt;img onerror=x&gt;" in support["html"]
    assert "&lt;tg-button&gt;" in support["html"]


def test_rich_draft_calls_bot_api_with_stop_and_stable_draft_id() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class Bot:
            async def _post(self, endpoint, data):
                calls.append((endpoint, data))
                return True

        service = TelegramRichMessageService()
        assert await service.send_draft(
            Bot(), 42, 123, {"html": "<tg-thinking>Thinking…</tg-thinking>"},
            can_stop=True, message_thread_id=8,
        )
        assert calls == [(
            "sendRichMessageDraft",
            {
                "chat_id": 42,
                "draft_id": 123,
                "rich_message": {"html": "<tg-thinking>Thinking…</tg-thinking>"},
                "can_stop": True,
                "keep_on_stop": True,
                "message_thread_id": 8,
            },
        )]

    asyncio.run(scenario())


def test_rich_draft_retries_without_bot_api_10_3_stop_flags() -> None:
    async def scenario() -> None:
        calls: list[dict[str, object]] = []

        class Bot:
            async def _post(self, _endpoint, data):
                calls.append(dict(data))
                if "can_stop" in data:
                    raise BadRequest("Unknown parameter can_stop")
                return True

        service = TelegramRichMessageService()
        assert await service.send_draft(Bot(), 42, 123, {"markdown": "Partial"}, can_stop=True)
        assert "can_stop" in calls[0]
        assert "can_stop" not in calls[1]
        assert calls[0]["draft_id"] == calls[1]["draft_id"] == 123

    asyncio.run(scenario())


def test_assistant_rich_send_falls_back_on_unsupported_method() -> None:
    async def scenario() -> None:
        sent: list[str] = []

        class Bot:
            async def _post(self, _endpoint, _data):
                raise BadRequest("Method not found")

        class Message:
            chat_id = 42
            message_id = 99
            message_thread_id = None

            def get_bot(self):
                return Bot()

            async def reply_text(self, text):
                sent.append(text)

        await TelegramRichMessageService().reply_assistant(Message(), "বাংলা উত্তর")
        assert sent == ["✨ AI RESPONSE\n\nবাংলা উত্তর"]

    asyncio.run(scenario())


def test_admin_reply_uses_the_same_rich_send_service() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class Bot:
            async def _post(self, endpoint, data):
                calls.append((endpoint, data))
                return True

        await TelegramRichMessageService().send_admin_reply(Bot(), 42, "We fixed it.", 42)
        assert len(calls) == 1
        endpoint, data = calls[0]
        assert endpoint == "sendRichMessage"
        markdown = data["rich_message"]["markdown"]
        assert "ADMIN RESPONSE" in markdown
        assert "Support request #42" in markdown
        assert "We fixed it." in markdown

    asyncio.run(scenario())


def test_document_exports_markdown_text_and_searchable_bangla_pdf() -> None:
    history = [
        {"role": "user", "content": "বাংলা প্রশ্ন"},
        {"role": "assistant", "content": "উত্তর: যুক্তাক্ষর ক্ষ, স্থ, দ্ধ"},
    ]
    name, markdown, mime = DocumentExportService.render(history, "markdown")
    assert name.endswith(".md") and mime == "text/markdown"
    assert "## You" in markdown.decode("utf-8")
    name, plain, mime = DocumentExportService.render(history, "txt")
    assert name.endswith(".txt") and mime == "text/plain"
    assert "বাংলা প্রশ্ন" in plain.decode("utf-8")
    name, pdf, mime = DocumentExportService.render(history, "pdf")
    assert name.endswith(".pdf") and mime == "application/pdf"
    assert pdf.startswith(b"%PDF-")
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "বাংলা প্রশ্ন" in extracted
    assert "যুক্তাক্ষর" in extracted


def test_extracted_document_report_exports_markdown_and_text_safely() -> None:
    chunks = [
        {"source": "page 1", "content": "বাংলা document body"},
        {"source": "page 2", "content": "Second page content"},
    ]
    name, markdown, mime = DocumentExportService.render_document(
        "..\\private report?.pdf", "application/pdf", chunks, "md",
    )
    assert name == "private_report.md"
    assert mime == "text/markdown"
    assert "## page 1" in markdown.decode("utf-8")
    name, text, mime = DocumentExportService.render_document(
        "report.pdf", "application/pdf", chunks, "txt",
    )
    assert name == "report.txt" and mime == "text/plain"
    assert "page 2" in text.decode("utf-8")
    with pytest.raises(ValueError, match="md or txt"):
        DocumentExportService.render_document("report.pdf", "application/pdf", chunks, "pdf")


def test_telegram_stop_update_cancels_only_matching_active_draft() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        matching = asyncio.create_task(asyncio.sleep(60))
        unrelated = asyncio.create_task(asyncio.sleep(60))
        handlers.active_generations = {42: (99, matching), 43: (88, unrelated)}
        handlers.partial_generations = {}
        update = SimpleNamespace(api_kwargs={
            "stopped_message_generation": {
                "chat": {"id": 42, "type": "private"}, "draft_id": 99,
            },
        })
        await handlers.on_extension_update(update, SimpleNamespace())
        await asyncio.sleep(0)
        assert matching.cancelled()
        assert not unrelated.cancelled()
        unrelated.cancel()
        await asyncio.gather(unrelated, return_exceptions=True)

    asyncio.run(scenario())


def test_ptb_preserves_unknown_stop_update_for_compatibility_handler() -> None:
    update = Update.de_json({
        "update_id": 5,
        "stopped_message_generation": {
            "chat": {"id": 42, "type": "private"},
            "draft_id": 99,
        },
    })
    assert update is not None
    assert update.api_kwargs["stopped_message_generation"]["draft_id"] == 99


def test_authorized_admin_reply_is_routed_through_shared_renderer() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        audit: list[tuple[object, ...]] = []

        class Database:
            async def is_user_blocked(self, _user_id):
                return False

            async def admin_reply_context(self, message_id):
                assert message_id == 111
                return {"user_chat_id": 42, "user_id": 42, "theme_id": "chemistry"}

            async def is_live(self, user_id):
                return user_id == 42

            async def audit(self, *args):
                audit.append(args)

        class Bot:
            async def _post(self, endpoint, data):
                calls.append((endpoint, data))
                return True

        class AdminMessage:
            text = "The issue is fixed."
            reply_to_message = SimpleNamespace(message_id=111)

            async def reply_text(self, text):
                self.ack = text

        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            admin_chat_id=777, effective_admin_user_ids=frozenset({9}),
            access_mode="public", allowed_user_ids=frozenset(),
        )
        handlers.db = Database()
        handlers.messages = TelegramRichMessageService()
        handlers.theme_engine = AdaptiveThemeEngine()
        message = AdminMessage()
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=9, is_bot=False, full_name="Admin"),
            effective_chat=SimpleNamespace(id=777, type="private"),
        )
        await handlers.on_message(update, SimpleNamespace(bot=Bot()))
        assert calls[0][0] == "sendRichMessage"
        assert "ADMIN RESPONSE" in calls[0][1]["rich_message"]["markdown"]
        assert "অণু গবেষণাগার" in calls[0][1]["rich_message"]["markdown"]
        assert audit == [(9, "admin.reply", "42", "delivered")]
        assert "delivered" in message.ack

    asyncio.run(scenario())


def test_unapproved_sender_cannot_send_support_reply() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class Database:
            async def is_user_blocked(self, _user_id):
                return False

        class Bot:
            async def _post(self, endpoint, data):
                calls.append((endpoint, data))
                return True

        class AdminMessage:
            text = "Pretend to be admin"
            reply_to_message = SimpleNamespace(message_id=111)

            async def reply_text(self, _text):
                raise AssertionError("Unauthorized sender should not reach a support reply")

        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            admin_chat_id=777, effective_admin_user_ids=frozenset({9}),
            access_mode="public", allowed_user_ids=frozenset(),
        )
        handlers.db = Database()
        handlers.messages = TelegramRichMessageService()
        update = SimpleNamespace(
            effective_message=AdminMessage(),
            effective_user=SimpleNamespace(id=10, is_bot=False, full_name="Other user"),
            effective_chat=SimpleNamespace(id=777, type="private"),
        )
        await handlers.on_message(update, SimpleNamespace(bot=Bot()))
        assert calls == []

    asyncio.run(scenario())


def test_cancelled_provider_returns_partial_text_to_the_final_path() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers.active_generations = {}
        handlers.partial_generations = {}
        result_task = asyncio.create_task(
            handlers._run_generation(42, 99, asyncio.sleep(60), "openrouter::model")
        )
        await asyncio.sleep(0)
        handlers.partial_generations[42] = "Generated portion"
        handlers.active_generations[42][1].cancel()
        result = await result_task
        assert result.text == "Generated portion"
        assert result.model == "openrouter::model"

    asyncio.run(scenario())


def test_invalid_export_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="Choose one of"):
        DocumentExportService.render([{"role": "user", "content": "x"}], "exe")
