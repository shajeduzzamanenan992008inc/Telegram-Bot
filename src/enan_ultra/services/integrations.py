"""Explicitly allowlisted Home Assistant and self-service VM controls."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
from collections import deque
from typing import Any
from urllib.parse import quote

import httpx

from enan_ultra.config import Settings

logger = logging.getLogger(__name__)
_APP_LOGS: deque[str] = deque(maxlen=200)
_SECRET_LOG = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|(?:api[_-]?key|token|password|secret)\s*[:=]\s*)([^\s,;}\"]+)"
)


class _SanitizedRingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record).replace("\r", " ").replace("\n", " ")
            message = _SECRET_LOG.sub(r"\1[REDACTED]", message)
            _APP_LOGS.append(message[:300])
        except Exception:
            return


def install_sanitized_log_buffer() -> None:
    target = logging.getLogger("enan_ultra")
    if not any(isinstance(item, _SanitizedRingHandler) for item in target.handlers):
        handler = _SanitizedRingHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        target.addHandler(handler)


def sanitized_app_logs(lines: int = 30) -> list[str]:
    return list(_APP_LOGS)[-max(1, min(lines, 50)):]


class HomeAssistantClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client: httpx.AsyncClient | None = None
        if settings.ha_base_url and settings.ha_token:
            self.client = httpx.AsyncClient(
                base_url=settings.ha_base_url,
                headers={"Authorization": f"Bearer {settings.ha_token}", "Content-Type": "application/json"},
                timeout=httpx.Timeout(connect=5, read=15, write=10, pool=5), follow_redirects=False,
            )

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()

    async def states(self) -> list[dict[str, str]]:
        if not self.client:
            raise RuntimeError("Home Assistant integration is not configured")
        semaphore = asyncio.Semaphore(10)

        async def fetch(entity_id: str) -> dict[str, str] | None:
            async with semaphore:
                response = await self.client.get(f"/api/states/{quote(entity_id, safe='')}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            item = response.json()
            return {"entity_id": entity_id, "state": str(item.get("state", "unknown"))[:80]}

        results = await asyncio.gather(*(fetch(entity) for entity in self.settings.ha_allowed_entities))
        return [item for item in results if item is not None]

    async def call_service(self, service: str, entity_ids: list[str], service_data: dict[str, Any]) -> None:
        if not self.client:
            raise RuntimeError("Home Assistant integration is not configured")
        if service not in self.settings.ha_allowed_services:
            raise ValueError("Home Assistant service is not allowlisted")
        allowed_entities = set(self.settings.ha_allowed_entities)
        if not entity_ids or any(entity not in allowed_entities for entity in entity_ids):
            raise ValueError("One or more Home Assistant entities are not allowlisted")
        domain, action = service.split(".", 1)
        response = await self.client.post(
            f"/api/services/{quote(domain, safe='')}/{quote(action, safe='')}",
            json={"entity_id": entity_ids, **service_data},
        )
        response.raise_for_status()


class VMControl:
    """Only inspect this service and request its systemd-managed restart."""

    _UNIT = "telegram-ai.service"

    async def status(self) -> dict[str, str]:
        if os.name != "posix":
            return {"service": "telegram-ai.service", "state": "VM controls require Linux systemd"}
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/systemctl", "is-active", self._UNIT,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        state = stdout.decode("utf-8", "replace").strip()[:40] or "unknown"
        return {"service": self._UNIT, "state": state}

    async def sanitized_logs(self, lines: int = 30) -> list[str]:
        return sanitized_app_logs(lines)

    def request_restart(self) -> None:
        if os.name != "posix" or not hasattr(signal, "SIGTERM"):
            raise RuntimeError("Managed restart is supported only by the Linux systemd service")
        # systemd Restart=always restarts clean process exits too. PTB handles SIGTERM and
        # closes the database/provider clients before the process exits.
        loop = asyncio.get_running_loop()
        loop.call_later(2.0, os.kill, os.getpid(), signal.SIGTERM)
