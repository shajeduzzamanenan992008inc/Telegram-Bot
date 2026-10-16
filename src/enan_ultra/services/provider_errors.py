"""Safe, provider-neutral failure classification and HTTP retry metadata."""

from __future__ import annotations

import re
import time
import math
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any


@dataclass(slots=True)
class AttemptBudget:
    maximum: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.maximum - self.used)

    def consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.used += 1
        return True


_ATTEMPT_BUDGET: ContextVar[AttemptBudget | None] = ContextVar("ai_provider_attempt_budget", default=None)


def install_attempt_budget(maximum: int) -> Token[AttemptBudget | None]:
    return _ATTEMPT_BUDGET.set(AttemptBudget(maximum=max(1, maximum)))


def reset_attempt_budget(token: Token[AttemptBudget | None]) -> None:
    _ATTEMPT_BUDGET.reset(token)


def consume_provider_attempt() -> bool:
    budget = _ATTEMPT_BUDGET.get()
    return budget.consume() if budget else True


def remaining_provider_attempts() -> int | None:
    budget = _ATTEMPT_BUDGET.get()
    return budget.remaining if budget else None


class ProviderError(RuntimeError):
    """Provider failure without request bodies, credentials, or user content."""

    def __init__(
        self, message: str, status_code: int | None = None, *, category: str | None = None,
        retry_after: float | None = None, request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category or category_for_status(status_code)
        self.retry_after = retry_after
        self.request_id = request_id


def category_for_status(status_code: int | None) -> str:
    if status_code == 401:
        return "invalid_credential"
    if status_code == 402:
        return "quota_or_billing"
    if status_code == 429:
        return "rate_limited"
    if status_code in {400, 413, 422}:
        return "invalid_request"
    if status_code == 404:
        return "model_unavailable"
    if status_code in {408, 425} or (status_code is not None and status_code >= 500):
        return "temporary_unavailable"
    return "provider_rejected"


def retry_after_seconds(headers: Any) -> float | None:
    """Parse Retry-After and common reset headers, including HTTP dates."""
    candidates = (
        headers.get("retry-after"),
        headers.get("retry-after-ms"),
        headers.get("x-ratelimit-reset-requests"),
        headers.get("x-ratelimit-reset"),
    )
    for index, raw in enumerate(candidates):
        if raw is None:
            continue
        value = str(raw).strip()
        if not value:
            continue
        try:
            seconds = float(value) / (1000 if index == 1 else 1)
            if index >= 2 and seconds > 86_400:
                seconds -= time.time()
        except ValueError:
            normalized = value.casefold()
            durations = re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)", normalized)
            if durations and "".join(f"{amount}{unit}" for amount, unit in durations) == normalized:
                seconds = sum(
                    float(amount) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
                    for amount, unit in durations
                )
            else:
                try:
                    reset_at = parsedate_to_datetime(value)
                    if reset_at.tzinfo is None:
                        reset_at = reset_at.replace(tzinfo=UTC)
                    seconds = (reset_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    return None


def error_from_response(provider: str, response: Any) -> ProviderError:
    status = int(response.status_code)
    category = category_for_status(status)
    try:
        payload = response.json()
        encoded = str(payload).casefold()[:4000]
    except Exception:
        encoded = str(getattr(response, "text", "")).casefold()[:4000]
    if status == 403 and any(term in encoded for term in ("invalid api key", "invalid token", "revoked key")):
        category = "invalid_credential"
    elif status in {400, 404} and "model" in encoded and any(
        term in encoded for term in ("not found", "not available", "unsupported", "unknown model")
    ):
        category = "model_unavailable"
    elif status == 400 and any(term in encoded for term in ("quota", "billing", "credit balance")):
        category = "quota_or_billing"
    request_id = (
        response.headers.get("x-request-id") or response.headers.get("request-id")
        or response.headers.get("cf-ray")
    )
    return ProviderError(
        f"{provider} {category.replace('_', ' ')} (HTTP {status})", status,
        category=category, retry_after=retry_after_seconds(response.headers),
        request_id=request_id[:128] if request_id else None,
    )
