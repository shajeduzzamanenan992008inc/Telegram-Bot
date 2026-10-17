from __future__ import annotations

import asyncio
import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from enan_ultra.handlers import BotHandlers
from enan_ultra.services.antivirus import AntivirusUnavailable, MalwareDetected, scan_upload


class FakeWriter:
    def __init__(self) -> None:
        self.payload = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.payload.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def test_clamav_instream_protocol_chunks_data_and_accepts_clean_result(monkeypatch) -> None:
    async def scenario() -> None:
        writer = FakeWriter()
        opened_paths: list[str] = []

        reader = asyncio.StreamReader()
        reader.feed_data(b"stream: OK\0")
        reader.feed_eof()

        async def open_connection_with_reply(path: str):
            opened_paths.append(path)
            return reader, writer

        monkeypatch.setattr(
            asyncio, "open_unix_connection", open_connection_with_reply, raising=False,
        )
        payload = b"a" * (64 * 1024 + 7)
        await scan_upload(payload, Path("/run/clamav/clamd.ctl"))

        assert opened_paths == [str(Path("/run/clamav/clamd.ctl"))]
        assert writer.payload.startswith(b"zINSTREAM\0")
        encoded = bytes(writer.payload[len(b"zINSTREAM\0"):])
        first_size = struct.unpack("!I", encoded[:4])[0]
        first_chunk = encoded[4:4 + first_size]
        second_offset = 4 + first_size
        second_size = struct.unpack("!I", encoded[second_offset:second_offset + 4])[0]
        second_chunk = encoded[second_offset + 4:second_offset + 4 + second_size]
        terminator_offset = second_offset + 4 + second_size
        assert first_chunk + second_chunk == payload
        assert encoded[terminator_offset:] == b"\0\0\0\0"
        assert writer.closed

    asyncio.run(scenario())


def test_clamav_detected_malware_is_reported_as_blocked(monkeypatch) -> None:
    async def scenario() -> None:
        async def open_connection(_path: str):
            reader = asyncio.StreamReader()
            reader.feed_data(b"stream: Eicar-Test-Signature FOUND\0")
            reader.feed_eof()
            return reader, FakeWriter()

        monkeypatch.setattr(asyncio, "open_unix_connection", open_connection, raising=False)
        with pytest.raises(MalwareDetected):
            await scan_upload(b"eicar-test-data", "/run/clamav/clamd.ctl")

    asyncio.run(scenario())


def test_clamav_unavailable_fails_closed(monkeypatch) -> None:
    async def scenario() -> None:
        async def open_connection(_path: str):
            raise FileNotFoundError("socket not present")

        monkeypatch.setattr(asyncio, "open_unix_connection", open_connection, raising=False)
        with pytest.raises(AntivirusUnavailable) as error:
            await scan_upload(b"unscanned upload", "/run/clamav/clamd.ctl")
        assert error.value.category == "socket_missing"

    asyncio.run(scenario())


def test_unexpected_clamav_reply_is_not_treated_as_clean(monkeypatch) -> None:
    async def scenario() -> None:
        async def open_connection(_path: str):
            reader = asyncio.StreamReader()
            reader.feed_data(b"stream: scan limit exceeded ERROR\0")
            reader.feed_eof()
            return reader, FakeWriter()

        monkeypatch.setattr(asyncio, "open_unix_connection", open_connection, raising=False)
        with pytest.raises(AntivirusUnavailable) as error:
            await scan_upload(b"oversized scanner stream", "/run/clamav/clamd.ctl")
        assert error.value.category == "invalid_response"

    asyncio.run(scenario())


def test_clamav_timeout_and_missing_configuration_have_safe_categories(monkeypatch) -> None:
    async def scenario() -> None:
        async def stalled_connection(_path: str):
            await asyncio.sleep(1)

        monkeypatch.setattr(
            asyncio, "open_unix_connection", stalled_connection, raising=False,
        )
        with pytest.raises(AntivirusUnavailable) as timeout_error:
            await scan_upload(b"upload", "/run/clamav/clamd.ctl", timeout=0.001)
        assert timeout_error.value.category == "timeout"

        with pytest.raises(AntivirusUnavailable) as config_error:
            await scan_upload(b"upload", "")
        assert config_error.value.category == "not_configured"

    asyncio.run(scenario())


def test_telegram_download_scans_before_return_and_alerts_without_file_contents(monkeypatch) -> None:
    async def scenario() -> None:
        from enan_ultra import handlers as handlers_module

        payload = b"malware-looking-private-upload"
        scanned: list[tuple[bytes, str]] = []

        async def infected_scan(data: bytes, socket_path: str, **_kwargs) -> None:
            scanned.append((data, socket_path))
            raise MalwareDetected("infected")

        monkeypatch.setattr(handlers_module, "scan_upload", infected_scan)
        handlers = BotHandlers(
            SimpleNamespace(
                malware_scan_enabled=True, clamav_socket_path=Path("/run/clamav/clamd.ctl"),
                admin_alerts=True, admin_chat_id=123,
            ),
            None, None, None, None, None,
        )
        handlers._notify_malware_detection = AsyncMock()

        class TelegramFile:
            file_size = len(payload)

            async def download_as_bytearray(self) -> bytearray:
                return bytearray(payload)

        class Bot:
            async def get_file(self, _file_id: str) -> TelegramFile:
                return TelegramFile()

        bot = Bot()
        with pytest.raises(MalwareDetected):
            await handlers._download(SimpleNamespace(bot=bot), "telegram-file-id", 1024)

        expected_digest = hashlib.sha256(payload).hexdigest()[:16]
        assert scanned == [(payload, Path("/run/clamav/clamd.ctl"))]
        handlers._notify_malware_detection.assert_awaited_once_with(
            bot, expected_digest,
        )

    asyncio.run(scenario())
