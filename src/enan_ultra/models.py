"""Shared immutable contracts between the assistant subsystems."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


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
    provider: str = ""
    latency_ms: int | None = None
    correlation_id: str = ""


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
    status: Literal["done", "unverified", "failed"] | None = None
    tool_name: str = ""
    operation_id: str = ""
    duration_ms: int = 0
    diagnostic: str = ""
    verification_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is None:
            object.__setattr__(self, "status", "done" if self.ok else "failed")
        elif self.status not in {"done", "unverified", "failed"}:
            raise ValueError("Invalid action outcome status")
        if self.ok != (self.status != "failed"):
            raise ValueError("Action success flag does not match its outcome status")
        if self.duration_ms < 0:
            raise ValueError("Action duration cannot be negative")

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "operation_id": self.operation_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "result": {"message": self.message, "data": self.data},
            "diagnostic": self.diagnostic,
            "verification_evidence": list(self.verification_evidence),
        }
