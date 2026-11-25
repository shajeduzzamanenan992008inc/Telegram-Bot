"""Regression tests for DNS pinning and response bounds in the /read fetcher."""

from __future__ import annotations

import asyncio
import socket

import pytest

from enan_ultra import security
from enan_ultra.services import web_reader


def _address(value: str):
    family = socket.AF_INET6 if ":" in value else socket.AF_INET
    sockaddr = (value, 443, 0, 0) if family == socket.AF_INET6 else (value, 443)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr


class _DocumentResponse:
    def __init__(
        self, *, status: int = 200, content_type: str = "text/html; charset=utf-8",
        encoding: str = "identity", length: int | None = None, chunks: tuple[bytes, ...] = (),
    ) -> None:
        self.status = status
        self.length = length
        self._content_type = content_type
        self._encoding = encoding
        self._chunks = list(chunks)
        self.reads: list[int] = []

    def getheader(self, name: str, default: str = "") -> str:
        if name == "Content-Type":
            return self._content_type
        if name == "Content-Encoding":
            return self._encoding
        return default

    def read1(self, size: int) -> bytes:
        self.reads.append(size)
        return self._chunks.pop(0) if self._chunks else b""


def _install_connection(monkeypatch: pytest.MonkeyPatch, response: _DocumentResponse) -> dict:
    observed: dict[str, object] = {}

    class FakeConnection:
        def __init__(self, host, addresses, timeout) -> None:
            observed["host"] = host
            observed["addresses"] = addresses
            observed["timeout"] = timeout
            observed["closed"] = False
            self.sock = None

        def request(self, method, target, *, headers):
            observed["request"] = (method, target, headers)

        def getresponse(self):
            return response

        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setattr(security, "_PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr(
        security.socket, "getaddrinfo",
        lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )
    return observed


def test_reader_rejects_redirects_without_following_or_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _DocumentResponse(status=302, length=0)
    observed = _install_connection(monkeypatch, response)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="Redirects are not followed"):
            await web_reader.fetch_page_text("https://example.com/page")

    asyncio.run(scenario())
    assert response.reads == []
    assert observed["closed"] is True


@pytest.mark.parametrize("content_type", ["application/json", "text/plain", "image/png"])
def test_reader_rejects_non_html_content_types(
    monkeypatch: pytest.MonkeyPatch, content_type: str,
) -> None:
    _install_connection(monkeypatch, _DocumentResponse(content_type=content_type, length=0))

    async def scenario() -> None:
        with pytest.raises(ValueError, match="did not return an HTML page"):
            await web_reader.fetch_page_text("https://example.com/page")

    asyncio.run(scenario())


def test_reader_rejects_compressed_responses_to_avoid_decompression_bombs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch, _DocumentResponse(encoding="gzip", length=10))

    async def scenario() -> None:
        with pytest.raises(ValueError, match="unsupported content encoding"):
            await web_reader.fetch_page_text("https://example.com/page")

    asyncio.run(scenario())


def test_reader_rejects_declared_and_streamed_oversize_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(
        monkeypatch, _DocumentResponse(length=web_reader.MAX_HTML_BYTES + 1),
    )
    with pytest.raises(ValueError, match="2 MiB"):
        security._download_public_https_document(
            "https://example.com/page", web_reader.MAX_HTML_BYTES, 5.0,
        )

    response = _DocumentResponse(chunks=(b"x" * (web_reader.MAX_HTML_BYTES + 1),))
    _install_connection(monkeypatch, response)
    with pytest.raises(ValueError, match="2 MiB"):
        security._download_public_https_document(
            "https://example.com/page", web_reader.MAX_HTML_BYTES, 5.0,
        )
    assert response.reads


def test_reader_bounds_the_read_by_the_response_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch, _DocumentResponse(length=10))
    ticks = iter((0.0, 100.0))
    monkeypatch.setattr(security.time, "monotonic", lambda: next(ticks, 100.0))

    with pytest.raises(TimeoutError, match="time limit"):
        security._download_public_https_document("https://example.com/page", 1024, 5.0)


def test_reader_pins_the_validated_dns_answer_against_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    public = _address("93.184.216.34")

    def changing_dns(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [public] if calls == 1 else [_address("127.0.0.1")]

    observed = _install_connection(
        monkeypatch, _DocumentResponse(length=18, chunks=(b"<main>Hello</main>",)),
    )
    monkeypatch.setattr(security.socket, "getaddrinfo", changing_dns)

    body = asyncio.run(
        security.fetch_public_https_document("https://example.com/page", max_bytes=1024),
    )

    assert body == b"<main>Hello</main>"
    assert calls == 1
    assert observed["addresses"] == (public,)
    method, target, headers = observed["request"]
    assert (method, target) == ("GET", "/page")
    assert headers["Host"] == "example.com"
    assert headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("target", ["127.0.0.1", "[::1]", "169.254.169.254"])
def test_reader_rejects_private_literal_hosts_before_connecting(
    monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    def forbidden_connection(*_args, **_kwargs):
        raise AssertionError("private targets must never open a connection")

    monkeypatch.setattr(security, "_PinnedHTTPSConnection", forbidden_connection)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="Private or reserved"):
            await web_reader.fetch_page_text(f"https://{target}/page")

    asyncio.run(scenario())


def test_reader_rejects_hostnames_resolving_only_to_private_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security.socket, "getaddrinfo", lambda *_args, **_kwargs: [_address("10.0.0.5")],
    )

    async def scenario() -> None:
        with pytest.raises(ValueError, match="Private or reserved"):
            await web_reader.fetch_page_text("https://intranet.example/page")

    asyncio.run(scenario())


def test_reader_reports_unsuccessful_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_connection(monkeypatch, _DocumentResponse(status=503, length=0))

    async def scenario() -> None:
        with pytest.raises(ValueError, match="unsuccessful status"):
            await web_reader.fetch_page_text("https://example.com/page")

    asyncio.run(scenario())


def test_reader_returns_extracted_text_for_a_valid_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(
        monkeypatch,
        _DocumentResponse(length=38, chunks=(b"<main><h1>Title</h1><p>Body</p></main>",)),
    )
    assert asyncio.run(web_reader.fetch_page_text("https://example.com/page")) == "Title Body"
