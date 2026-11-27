"""Bounded audio/video preprocessing using ffmpeg tools without executing uploads."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from enan_ultra.config import Settings
from enan_ultra.services.openrouter import ProviderError
from enan_ultra.services.providers import ProviderRouter


@dataclass(frozen=True, slots=True)
class VideoMedia:
    frames: tuple[bytes, ...]
    audio: bytes
    duration: float


class MediaProcessor:
    def __init__(self, settings: Settings, router: ProviderRouter) -> None:
        self.settings = settings
        self.router = router

    async def transcribe(self, data: bytes, filename: str, mime_type: str) -> str:
        if not self.settings.voice_input_enabled:
            raise ProviderError("Voice input is disabled")
        duration = await asyncio.to_thread(self._probe_duration, data, filename)
        if duration is None:
            raise ValueError("Audio duration could not be validated; ffprobe is required")
        if duration is not None and duration > self.settings.max_audio_seconds:
            raise ValueError("Audio is longer than MAX_AUDIO_SECONDS")
        return await self.router.transcribe(data, filename[:120], mime_type)

    async def video(self, data: bytes, filename: str) -> VideoMedia:
        return await asyncio.to_thread(self._video_sync, data, filename)

    def _video_sync(self, data: bytes, filename: str) -> VideoMedia:
        if not self._binary("ffmpeg") or not self._binary("ffprobe"):
            raise ValueError("Video reading requires ffmpeg and ffprobe")
        suffix = Path(filename).suffix[:12] or ".mp4"
        with tempfile.TemporaryDirectory(prefix="enan-video-") as folder:
            source = Path(folder) / f"input{suffix}"
            source.write_bytes(data)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
                capture_output=True, timeout=20, check=False,
            )
            if probe.returncode != 0:
                raise ValueError("Video metadata could not be read")
            duration = float(json.loads(probe.stdout or b"{}").get("format", {}).get("duration", 0))
            if duration <= 0 or duration > self.settings.max_video_seconds:
                raise ValueError("Video duration is outside the configured limit")

            fps = min(self.settings.max_video_frames / duration, 1.0)
            out_pattern = str(Path(folder) / "frame-%02d.jpg")
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-vf", f"fps={fps:.6f},scale=1280:1280:force_original_aspect_ratio=decrease",
                 "-frames:v", str(self.settings.max_video_frames), "-q:v", "4", out_pattern],
                capture_output=True, timeout=45, check=False,
            )
            frames = tuple(
                content for path in sorted(Path(folder).glob("frame-*.jpg"))
                if len(content := path.read_bytes()) <= self.settings.max_image_bytes
            )
            audio = b""
            audio_path = Path(folder) / "audio.mp3"
            extracted = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-vn", "-t",
                 str(self.settings.max_video_seconds), "-ac", "1", "-ar", "16000", str(audio_path)],
                capture_output=True, timeout=45, check=False,
            )
            if extracted.returncode == 0 and audio_path.exists() and audio_path.stat().st_size <= self.settings.max_upload_bytes:
                audio = audio_path.read_bytes()
            return VideoMedia(frames, audio, duration)

    @staticmethod
    def _probe_duration(data: bytes, filename: str) -> float | None:
        if not MediaProcessor._binary("ffprobe"):
            return None
        suffix = Path(filename).suffix[:12] or ".audio"
        with tempfile.TemporaryDirectory(prefix="enan-audio-") as folder:
            source = Path(folder) / f"input{suffix}"
            source.write_bytes(data)
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
                capture_output=True, timeout=20, check=False,
            )
            if result.returncode:
                return None
            try:
                return float(json.loads(result.stdout or b"{}").get("format", {}).get("duration", 0))
            except (ValueError, TypeError):
                return None

    @staticmethod
    def _binary(name: str) -> bool:
        from shutil import which

        return which(name) is not None
