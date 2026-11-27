"""File adapter protocol and result contracts."""

from __future__ import annotations

from typing import Protocol

from enan_ultra.config import Settings
from enan_ultra.models import ContentChunk


class FileAdapter(Protocol):
    name: str

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool: ...

    async def extract(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> list[ContentChunk]: ...
