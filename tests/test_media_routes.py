from __future__ import annotations

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import httpx
from PIL import Image

from enan_ultra.handlers import BotHandlers
from enan_ultra.models import AssistantResult
from enan_ultra.services.openrouter import OpenRouter
from enan_ultra.services.providers import _OpenRouterAdapter


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(output, format="PNG")
    return output.getvalue()


def test_openrouter_image_generation_uses_current_endpoint_and_media_type() -> None:
    async def scenario() -> None:
        image_bytes = _png_bytes()
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/images/models"):
                return httpx.Response(200, json={"data": [{"id": "provider/image-model"}]})
            assert request.url.path.endswith("/images")
            payload = json.loads(request.content)
            assert payload == {
                "model": "provider/image-model", "prompt": "A green forest",
            }
            return httpx.Response(200, json={"data": [{
                "b64_json": base64.b64encode(image_bytes).decode("ascii"),
                "media_type": "image/png",
            }]})

        settings = SimpleNamespace(
            openrouter_keys=(), openrouter_base_url="https://openrouter.example/v1",
            display_name="Enan Ultra", image_model="removed/old-model",
            fallback_models=(), max_image_bytes=10_000,
        )
        client = OpenRouter(settings)
        client.client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url, transport=httpx.MockTransport(respond),
        )
        try:
            generated, media_type = await client.generate_image("A green forest")
            assert generated == image_bytes
            assert media_type == "image/png"
            assert [request.url.path for request in requests] == [
                "/v1/images/models", "/v1/images",
            ]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_openrouter_adapter_preserves_image_modality() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        class RouterStub:
            async def complete(self, _messages, **kwargs):
                calls.append(kwargs["modality"])
                return AssistantResult("analysis", "vision-model")

        adapter = _OpenRouterAdapter(RouterStub(), SimpleNamespace(default_model="auto"))
        await adapter.complete(
            "vision-model",
            [{"role": "user", "content": [
                {"type": "text", "text": "Analyze"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ]}],
            tools=None, max_tokens=100,
        )
        assert calls == ["image"]

    asyncio.run(scenario())


def test_streaming_chat_keeps_image_modality() -> None:
    async def scenario() -> None:
        calls: list[tuple[str, str]] = []

        class RouterStub:
            async def complete(self, _messages, **kwargs):
                calls.append((kwargs["modality"], kwargs["primary_model"]))
                return AssistantResult("analysis", kwargs["primary_model"])

        class DatabaseStub:
            async def get_setting(self, _key):
                return None

        handlers = object.__new__(BotHandlers)
        handlers.router = RouterStub()
        handlers.db = DatabaseStub()
        handlers.settings = SimpleNamespace(default_model="auto")
        handlers.active_generations = {}
        handlers.partial_generations = {}

        async def send_chat_action(*_args):
            return None

        update = SimpleNamespace(effective_chat=SimpleNamespace(id=9, type="group"))
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=send_chat_action))
        await handlers._stream_or_typing(
            update, context, [{"role": "user", "content": "image message"}],
            modality="image", primary_model="openrouter::configured-vision-model",
        )
        assert calls == [("image", "openrouter::configured-vision-model")]

    asyncio.run(scenario())


def test_openrouter_tts_uses_configured_model() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{
                    "id": "mistralai/voxtral-mini-tts-2603",
                    "architecture": {"output_modalities": ["audio"]},
                }]})
            assert request.url.path.endswith("/audio/speech")
            assert json.loads(request.content) == {
                "model": "mistralai/voxtral-mini-tts-2603",
                "input": "Hello there", "response_format": "mp3", "voice": "alloy",
            }
            return httpx.Response(200, content=b"generated-audio", headers={"content-type": "audio/mpeg"})

        settings = SimpleNamespace(
            openrouter_keys=(), openrouter_base_url="https://openrouter.example/v1",
            display_name="Enan Ultra", fallback_models=(),
            tts_model="mistralai/voxtral-mini-tts-2603", tts_voice="alloy",
            max_upload_bytes=10_000,
        )
        client = OpenRouter(settings)
        client.client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url, transport=httpx.MockTransport(respond),
        )
        try:
            assert await client.synthesize("Hello there") == b"generated-audio"
            assert [request.url.path for request in requests] == [
                "/v1/models", "/v1/audio/speech",
            ]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_voice_reply_uses_ai_synthesis_when_configured() -> None:
    async def scenario() -> None:
        class RouterStub:
            openrouter = object()

            async def synthesize(self, text, voice):
                assert text == "A Bengali reply"
                assert voice == "alloy"
                return b"ai-generated-audio"

        handlers = object.__new__(BotHandlers)
        handlers.router = RouterStub()
        handlers.settings = SimpleNamespace(
            max_upload_bytes=1_000, tts_model="configured-tts", tts_voice="alloy",
        )

        async def profile(_user_id, _name):
            return SimpleNamespace(local_only=False, language="bn")

        handlers._profile = profile
        received: list[str] = []

        async def reply_voice(audio_file):
            received.append(audio_file.filename)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7, full_name="Test"),
            effective_message=SimpleNamespace(reply_voice=reply_voice),
        )
        await handlers._send_voice(update, "A Bengali reply")
        assert received == ["reply.mp3"]

    asyncio.run(scenario())


def test_audio_handler_displays_transcript_and_chats_with_full_text() -> None:
    async def scenario() -> None:
        transcript = "ami programing sikta cai"
        replies: list[str] = []
        chat_calls: list[tuple[str, dict[str, object]]] = []

        class DatabaseStub:
            async def get_preference(self, _user_id, _key, default):
                return default

            async def get_setting(self, _key):
                return "configured-model"

        class RouterStub:
            async def models(self, **_kwargs):
                return []

            async def estimate_transcription_cost(self, *_args, **_kwargs):
                return 0

            async def estimate_cost(self, *_args, **_kwargs):
                return 0

        class MediaStub:
            async def transcribe(self, *_args):
                return transcript

        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            voice_input_enabled=True, max_upload_bytes=10_000, max_input_chars=1000,
            default_model="configured-model", voice_output_mode="off",
            tts_cost_reserve_usd=0, stt_model=None,
        )
        handlers.db = DatabaseStub()
        handlers.router = RouterStub()
        handlers.media = MediaStub()
        handlers._profile = lambda *_args: _async_value(
            SimpleNamespace(local_only=False, media_enabled=False),
        )

        async def request_allowed(*_args):
            return 17

        async def capture_chat(_update, _context, text, **kwargs):
            chat_calls.append((text, kwargs))

        handlers._request_allowed = request_allowed
        handlers._chat = capture_chat

        async def reply_text(text):
            replies.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=9, full_name="Test"),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )
        await handlers._handle_audio(
            update, SimpleNamespace(), "file-id", None, "audio/ogg", "voice.ogg", b"audio",
        )

        assert replies == ["🎙️ Transcript:\n" + transcript]
        assert chat_calls == [(transcript, {
            "voice_input": True, "usage_event_id": 17, "preapproved": True,
        })]

    async def _async_value(value):
        return value

    asyncio.run(scenario())


def test_file_parser_failure_is_reported_as_warning() -> None:
    from enan_ultra.files.registry import FileRegistry

    class BrokenParser:
        name = "test"

        def detect(self, _filename, _mime_type, _data):
            return True

        async def extract(self, *_args):
            raise RuntimeError("private parser detail")

    async def scenario() -> None:
        registry = FileRegistry()
        registry.adapters = [BrokenParser()]
        chunks, images = await registry.extract_bytes(
            "upload.bin", "application/octet-stream", b"\x00\xff",
            SimpleNamespace(max_upload_bytes=100, max_input_chars=100),
        )
        assert images == ()
        assert len(chunks) == 1
        assert chunks[0].modality == "warning"
        assert "parser failed safely (RuntimeError)" in chunks[0].text
        assert "private parser detail" not in chunks[0].text

    asyncio.run(scenario())
