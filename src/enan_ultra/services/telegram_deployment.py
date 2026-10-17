"""Client for the optional, root-owned, narrowly scoped systemd deployment broker."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any


class TelegramDeploymentClient:
    def __init__(self, socket_path: Path, *, timeout_seconds: float = 8.0) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    async def preflight(self) -> dict[str, Any]:
        response = await self._request({"operation": "preflight"})
        return response

    async def deploy(
        self,
        update_id: str,
        manifest_hash: str,
        archive_hash: str,
        administrator_id: int,
    ) -> dict[str, Any]:
        return await self._request({
            "operation": "deploy",
            "update_id": update_id,
            "manifest_hash": manifest_hash,
            "archive_hash": archive_hash,
            "administrator_id": administrator_id,
        })

    async def recent_results(self) -> list[dict[str, Any]]:
        response = await self._request({"operation": "recent"})
        results = response.get("results")
        return results if isinstance(results, list) else []

    async def acknowledge(self, update_id: str) -> bool:
        response = await self._request({"operation": "ack", "update_id": update_id})
        return response.get("acknowledged") is True

    async def _request(self, payload: dict[str, object]) -> dict[str, Any]:
        if os.name == "nt":
            raise RuntimeError("The systemd deployment broker is available only on Linux.")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=self.timeout_seconds,
            )
        except (OSError, TimeoutError) as exc:
            raise RuntimeError("The restricted deployment broker is unavailable.") from exc
        try:
            writer.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout_seconds)
            if not raw or len(raw) > 16 * 1024:
                raise RuntimeError("The deployment broker returned an invalid response.")
            response = json.loads(raw)
            if not isinstance(response, dict) or response.get("ok") is not True:
                error = response.get("error") if isinstance(response, dict) else None
                raise RuntimeError(
                    f"The deployment broker rejected the request ({str(error)[:80]})."
                )
            return response
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("The deployment broker request failed.") from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
