"""Cyclic API-key selection and failover for provider requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class KeyRotation:
    """Rotate the starting key per request and retry eligible HTTP failures once per key."""

    RETRYABLE_STATUS_CODES = frozenset({401, 402, 429})

    def __init__(self, provider: str, keys: tuple[str, ...]) -> None:
        self.provider = provider
        self.keys = keys
        self._next_index = 0
        self._lock = asyncio.Lock()

    async def run(self, operation: Callable[[str | None, int], Awaitable[T]]) -> T:
        if not self.keys:
            return await operation(None, -1)

        async with self._lock:
            start_index = self._next_index
            self._next_index = (self._next_index + 1) % len(self.keys)

        for offset in range(len(self.keys)):
            key_index = (start_index + offset) % len(self.keys)
            try:
                return await operation(self.keys[key_index], key_index)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code is None:
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                if status_code not in self.RETRYABLE_STATUS_CODES:
                    raise
                logger.warning(
                    "%s API key %d failed with HTTP %s; %s",
                    self.provider,
                    key_index + 1,
                    status_code,
                    "trying the next key" if offset + 1 < len(self.keys) else "no keys remain",
                )
                if offset + 1 == len(self.keys):
                    raise
        raise RuntimeError("Key rotation finished without a request result")
