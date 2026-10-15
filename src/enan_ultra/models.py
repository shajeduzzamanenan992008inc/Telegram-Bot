"""Shared immutable contracts between the assistant subsystems."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ContentChunk:
    text: str
    source: str
    modality: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    filename: str
    mime_type: str
    chunks: tuple[ContentChunk, ...]
    images: tuple[bytes, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n\n".join(f"[{chunk.source}]\n{chunk.text}" for chunk in self.chunks)


@dataclass(frozen=True, slots=True)
class AssistantResult:
    text: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[dict[str, Any], ...] = ()
    citations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UserProfile:
    language: str = "bn"
    name: str = ""
    style: str = "natural"
    voice_enabled: bool = False
    media_enabled: bool = True
    memory_enabled: bool = False
    local_only: bool = False
    agents_enabled: bool = True
    kb_enabled: bool = False
    study_enabled: bool = False
    response_format: str = "text"
    persona: str = "default"
    custom_instructions: str = ""


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
