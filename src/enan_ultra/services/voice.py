"""Asynchronous gTTS generation for Telegram voice replies."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from gtts import gTTS

logger = logging.getLogger(__name__)
MAX_VOICE_TEXT_CHARS = 10_000


async def generate_voice(text: str, voice_lang: str = "bn-BD") -> Path:
    """Generate an MP3 voice file off the event loop; caller must unlink the file."""
    content = text.strip()
    if not content:
        raise ValueError("Voice text must not be empty")
    if len(content) > MAX_VOICE_TEXT_CHARS:
        raise ValueError(f"Voice text exceeds {MAX_VOICE_TEXT_CHARS} characters")
    language = voice_lang.strip().replace("_", "-").split("-", 1)[0].lower()
    if not language.isalpha() or len(language) not in {2, 3}:
        raise ValueError("voice_lang must be a supported language code")

    with tempfile.NamedTemporaryFile(prefix="enan-voice-", suffix=".mp3", delete=False) as handle:
        output_path = Path(handle.name)
    try:
        await asyncio.to_thread(gTTS(text=content, lang=language).save, str(output_path))
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("Text-to-speech returned an empty audio file")
        return output_path
    except Exception:
        output_path.unlink(missing_ok=True)
        logger.warning("gTTS generation failed for language %s", language)
        raise
