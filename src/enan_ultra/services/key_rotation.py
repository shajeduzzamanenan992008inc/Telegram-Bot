"""Cyclic API-key selection and failover for provider requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import httpx

from enan_ultra.services.provider_errors import ProviderError

logger = logging.getLogger(__name__)
T = TypeVar("T")


def provider_state_path(settings: object, provider: str) -> Path | None:
    database_path = getattr(settings, "database_path", None)
    if not database_path:
        return None
    database_path = Path(database_path)
    return database_path.with_name(f"{database_path.name}.{provider}.credentials.json")


class KeyRotation:
    """Fairly select keys, quarantine rejected credentials, and retry transient failures safely."""

    # Authentication failures are credential-specific. Quota and rate limits are
    # provider/account scoped and must not be bypassed by cycling sibling keys.
    RETRYABLE_STATUS_CODES = frozenset({401})
    TRANSIENT_STATUS_CODES = frozenset({408, 425, 500, 502, 503, 504})

    def __init__(
        self, provider: str, keys: tuple[str, ...], *, max_retries_per_key: int = 0,
        state_path: Path | None = None, quota_cooldown_seconds: float = 60,
    ) -> None:
        self.provider = provider
        self.keys = tuple(dict.fromkeys(key.strip() for key in keys if key.strip()))
        self.max_retries_per_key = max(0, min(max_retries_per_key, 3))
        self._next_index = 0
        self._lock = asyncio.Lock()
        self._state_write_lock = asyncio.Lock()
        self._quarantined: set[int] = set()
        self._state_path = state_path
        self._quota_cooldown_seconds = max(1.0, min(float(quota_cooldown_seconds), 86_400.0))
        self._cooldown_until = 0.0
        self._cooldown_category: str | None = None
        self._load_quarantine()

    @staticmethod
    def fingerprint(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _load_quarantine(self) -> None:
        if not self._state_path or not self._state_path.is_file():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            fingerprints = set(payload.get("quarantined_fingerprints", []))
            self._quarantined = {
                index for index, key in enumerate(self.keys)
                if self.fingerprint(key) in fingerprints
            }
        except (OSError, ValueError, TypeError, AttributeError):
            logger.warning("Ignoring unreadable %s credential health state", self.provider)

    async def _persist_quarantine(self) -> None:
        if not self._state_path:
            return
        async with self._state_write_lock:
            payload = {
                "version": 1,
                "provider": self.provider,
                "quarantined_fingerprints": sorted(
                    self.fingerprint(self.keys[index]) for index in self._quarantined
                ),
            }

            def write() -> None:
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
                temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                os.replace(temporary, self._state_path)

            await asyncio.to_thread(write)

    async def clear_quarantine(self) -> None:
        async with self._lock:
            self._quarantined.clear()
            self._cooldown_until = 0.0
            self._cooldown_category = None
        await self._persist_quarantine()

    def diagnostics(self) -> dict[str, int | str | None]:
        cooldown_remaining = max(0, int(self._cooldown_until - time.monotonic()))
        return {
            "credentials": len(self.keys),
            "available_credentials": len(self.keys) - len(self._quarantined),
            "quarantined_credentials": len(self._quarantined),
            "cooldown_seconds": cooldown_remaining,
            "cooldown_category": self._cooldown_category if cooldown_remaining else None,
        }

    async def run(self, operation: Callable[[str | None, int], Awaitable[T]]) -> T:
        if not self.keys:
            return await operation(None, -1)

        async with self._lock:
            cooldown_remaining = self._cooldown_until - time.monotonic()
            if cooldown_remaining > 0:
                category = self._cooldown_category or "rate_limited"
                status = 402 if category == "quota_or_billing" else 429
                raise ProviderError(
                    f"{self.provider} is cooling down after {category.replace('_', ' ')}",
                    status, category=category, retry_after=cooldown_remaining,
                )
            start_index = self._next_index
            self._next_index = (self._next_index + 1) % len(self.keys)

        last_error: Exception | None = None
        for offset in range(len(self.keys)):
            key_index = (start_index + offset) % len(self.keys)
            if key_index in self._quarantined:
                continue
            for retry_index in range(self.max_retries_per_key + 1):
                try:
                    return await operation(self.keys[key_index], key_index)
                except Exception as exc:
                    last_error = exc
                    status_code = getattr(exc, "status_code", None)
                    category = getattr(exc, "category", None)
                    if status_code is None:
                        response = getattr(exc, "response", None)
                        status_code = getattr(response, "status_code", None)
                    if status_code == 401 or category == "invalid_credential":
                        async with self._lock:
                            self._quarantined.add(key_index)
                        await self._persist_quarantine()
                        logger.warning(
                            "%s credential %d was rejected and quarantined; trying another configured credential",
                            self.provider, key_index + 1,
                        )
                        break
                    if status_code in {402, 429} or category in {"quota_or_billing", "rate_limited"}:
                        cooldown_category = category or ("quota_or_billing" if status_code == 402 else "rate_limited")
                        retry_after = getattr(exc, "retry_after", None)
                        minimum = max(
                            self._quota_cooldown_seconds,
                            300.0 if cooldown_category == "quota_or_billing" else 0.0,
                        )
                        delay = max(
                            minimum,
                            float(retry_after) if isinstance(retry_after, (int, float)) else 0.0,
                        )
                        async with self._lock:
                            self._cooldown_until = max(
                                self._cooldown_until, time.monotonic() + delay,
                            )
                            self._cooldown_category = cooldown_category
                        raise
                    if getattr(exc, "partial_output", False):
                        raise
                    retryable = (
                        status_code in self.TRANSIENT_STATUS_CODES
                        or isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                        or category == "temporary_unavailable"
                    )
                    if not retryable or retry_index >= self.max_retries_per_key:
                        raise
                    retry_after = getattr(exc, "retry_after", None)
                    delay = min(2.0, 0.2 * (2**retry_index)) * random.uniform(0.75, 1.25)
                    if isinstance(retry_after, (int, float)):
                        delay = max(delay, min(float(retry_after), 2.0))
                    logger.info(
                        "%s transient request failure (%s); retry %d/%d on the same credential in %.2fs",
                        self.provider, type(exc).__name__, retry_index + 1,
                        self.max_retries_per_key, delay,
                    )
                    await asyncio.sleep(delay)
        if len(self._quarantined) == len(self.keys):
            raise ProviderError(
                f"All configured {self.provider} credentials were rejected",
                401, category="invalid_credential",
            ) from last_error
        if last_error:
            raise last_error
        raise RuntimeError("Key rotation finished without a request result")
