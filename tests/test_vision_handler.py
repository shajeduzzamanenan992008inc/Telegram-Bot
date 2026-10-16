from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

from PIL import Image

from enan_ultra.handlers import BotHandlers


def _image_bytes(image_format: str = "JPEG", size: tuple[int, int] = (64, 64)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "blue").save(output, format=image_format)
    return output.getvalue()


def _handlers(max_image_pixels: int = 10_000) -> BotHandlers:
    class DatabaseStub:
        async def is_live(self, _user_id: int) -> bool:
            return False

    handlers = BotHandlers(
        SimpleNamespace(
            max_image_bytes=1_000_000, max_upload_bytes=1_000_000,
            max_image_pixels=max_image_pixels, admin_chat_id=-1,
        ),
        DatabaseStub(), None, None, None, None,
    )

    async def allowed(_update: object) -> bool:
        return True

    async def no_handover(_update: object, _context: object) -> bool:
        return False

    handlers._gate = allowed
    handlers._trigger_handover = no_handover
    return handlers


def test_photo_handler_uses_highest_resolution_and_vision_prompt() -> None:
    async def scenario() -> None:
        handlers = _handlers()
        downloaded: list[tuple[str, int]] = []
        analyzed: list[tuple[object, ...]] = []
        data = _image_bytes()

        async def download(_context: object, file_id: str, limit: int) -> bytes:
            downloaded.append((file_id, limit))
            return data

        async def analyze(*args: object, **kwargs: object) -> None:
            analyzed.append((*args, kwargs))

        handlers._download = download
        handlers._chat = analyze
        message = SimpleNamespace(
            text=None,
            caption="What is in this picture?",
            photo=[
                SimpleNamespace(file_id="small-photo", file_size=100),
                SimpleNamespace(file_id="large-photo", file_size=200),
            ],
            reply_to_message=None,
            reply_text=lambda _text: None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=7, full_name="Test User", is_bot=False),
            effective_chat=SimpleNamespace(id=7, type="private"),
        )

        await handlers.on_message(update, SimpleNamespace())

        assert downloaded == [("large-photo", 1_000_000)]
        assert len(analyzed) == 1
        assert analyzed[0][2] == "What is in this picture?"
        assert analyzed[0][3] == {
            "image_data": data,
            "image_type": "image/jpeg",
        }

    asyncio.run(scenario())


def test_photo_handler_defaults_prompt_and_detects_image_mime() -> None:
    async def scenario() -> None:
        handlers = _handlers()
        data = _image_bytes("PNG")
        analyzed: list[tuple[object, ...]] = []

        async def download(_context: object, _file_id: str, _limit: int) -> bytes:
            return data

        async def analyze(*args: object, **kwargs: object) -> None:
            analyzed.append((*args, kwargs))

        handlers._download = download
        handlers._chat = analyze
        message = SimpleNamespace(
            text=None,
            caption=None,
            photo=[SimpleNamespace(file_id="photo", file_size=len(data))],
            reply_to_message=None,
            reply_text=lambda _text: None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=7, full_name="Test User", is_bot=False),
            effective_chat=SimpleNamespace(id=7, type="private"),
        )

        await handlers.on_message(update, SimpleNamespace())

        assert analyzed[0][2] == "Describe and analyze this image in detail in Bengali."
        assert analyzed[0][3]["image_type"] == "image/png"

    asyncio.run(scenario())


def test_photo_handler_rejects_images_over_pixel_limit() -> None:
    async def scenario() -> None:
        handlers = _handlers(max_image_pixels=1_000)
        data = _image_bytes(size=(64, 64))
        replies: list[str] = []
        analyzed: list[object] = []

        async def download(_context: object, _file_id: str, _limit: int) -> bytes:
            return data

        async def analyze(*args: object, **kwargs: object) -> None:
            analyzed.append((args, kwargs))

        async def reply_text(text: str) -> None:
            replies.append(text)

        handlers._download = download
        handlers._chat = analyze
        message = SimpleNamespace(
            text=None,
            caption=None,
            photo=[SimpleNamespace(file_id="large-image", file_size=len(data))],
            reply_to_message=None,
            reply_text=reply_text,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=7, full_name="Test User", is_bot=False),
            effective_chat=SimpleNamespace(id=7, type="private"),
        )

        await handlers.on_message(update, SimpleNamespace())

        assert not analyzed
        assert replies == [
            "I couldn't process that image. Please send a supported photo within the configured size limit."
        ]

    asyncio.run(scenario())
