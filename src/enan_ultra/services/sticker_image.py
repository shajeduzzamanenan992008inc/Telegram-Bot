"""Bounded image conversion for Telegram static WebP stickers."""

from __future__ import annotations

import asyncio
import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_STICKER_BYTES = 512 * 1024
MAX_STICKER_PIXELS = 40_000_000


async def create_sticker_webp(image_data: bytes) -> bytes:
    """Convert one uploaded raster image to a static WebP sticker no larger than 512 KiB."""
    return await asyncio.to_thread(_create_sticker_webp, image_data)


def _create_sticker_webp(image_data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(image_data)) as source:
            if source.width * source.height > MAX_STICKER_PIXELS:
                raise ValueError("Image dimensions exceed the sticker conversion limit")
            source.seek(0)
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("The uploaded file is not a supported image") from exc

    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    for quality in (85, 75, 65, 55, 45, 35):
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=quality, method=6)
        data = output.getvalue()
        if len(data) <= MAX_STICKER_BYTES:
            return data
    raise ValueError("The image could not be compressed to Telegram's sticker size limit")
