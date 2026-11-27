"""ClamAV daemon scanning for in-memory Telegram uploads."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path


class MalwareDetected(Exception):
    """Raised when ClamAV reports an infected upload."""


class AntivirusUnavailable(RuntimeError):
    """Raised when an enabled antivirus scanner cannot produce a clean result."""

    def __init__(self, message: str, *, category: str = "unavailable") -> None:
        super().__init__(message)
        self.category = category


async def scan_upload(
    data: bytes, socket_path: str | Path, *, timeout: float = 30.0,
) -> None:
    path = str(socket_path)
    if not path:
        raise AntivirusUnavailable(
            "ClamAV socket path is not configured", category="not_configured",
        )
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path), timeout=timeout,
        )
        writer.write(b"zINSTREAM\0")
        for offset in range(0, len(data), 64 * 1024):
            chunk = data[offset:offset + 64 * 1024]
            writer.write(struct.pack("!I", len(chunk)))
            writer.write(chunk)
            await writer.drain()
        writer.write(struct.pack("!I", 0))
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\0"), timeout=timeout)
    except (
        AttributeError, OSError, asyncio.TimeoutError,
        asyncio.IncompleteReadError, asyncio.LimitOverrunError,
    ) as exc:
        if isinstance(exc, asyncio.TimeoutError):
            category = "timeout"
        elif isinstance(exc, FileNotFoundError):
            category = "socket_missing"
        elif isinstance(exc, OSError):
            category = "socket_unreachable"
        else:
            category = "incomplete_response"
        raise AntivirusUnavailable(
            "ClamAV did not complete the upload scan", category=category,
        ) from exc
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    message = response.rstrip(b"\0").decode("utf-8", errors="replace")
    if message.endswith(" FOUND") and message.startswith("stream: "):
        raise MalwareDetected("ClamAV detected malware in an upload")
    if message != "stream: OK":
        raise AntivirusUnavailable(
            "ClamAV returned an invalid or unsuccessful scan result",
            category="invalid_response",
        )
