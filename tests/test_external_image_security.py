from __future__ import annotations

import asyncio
import socket

import pytest

from enan_ultra import security


def _address(value: str):
    family = socket.AF_INET6 if ":" in value else socket.AF_INET
    sockaddr = (value, 443, 0, 0) if family == socket.AF_INET6 else (value, 443)
    return family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr


@pytest.mark.parametrize("url", [
    "http://images.example/image.png",
    "https://user:pass@images.example/image.png",
    "https://images.example:444/image.png",
    "https://images.example:invalid/image.png",
    "https://[::1]/image.png",
    "https://127.0.0.1/image.png",
    "https://[fe80::1%25eth0]/image.png",
    "https://images.example\\@127.0.0.1/image.png",
    "https://images.example/line\nbreak.png",
])
def test_public_https_url_rejects_malformed_or_non_public_targets(url: str) -> None:
    with pytest.raises(ValueError):
        security.require_public_https_url(url)


@pytest.mark.parametrize("address", [
    "0.0.0.0", "10.1.2.3", "100.64.0.1", "127.0.0.1", "169.254.169.254",
    "192.0.2.1", "224.0.0.1", "::", "::1", "::ffff:127.0.0.1", "fc00::1",
    "fe80::1", "ff02::1", "2001:db8::1",
])
def test_dns_results_reject_prohibited_ipv4_and_ipv6_ranges(
    monkeypatch: pytest.MonkeyPatch, address: str,
) -> None:
    monkeypatch.setattr(security.socket, "getaddrinfo", lambda *_args, **_kwargs: [_address(address)])

    with pytest.raises(ValueError, match="Private or reserved"):
        security._resolve_public_https_target("https://images.example/image.png")


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001"])
def test_legacy_numeric_host_forms_are_blocked_after_system_resolution(
    monkeypatch: pytest.MonkeyPatch, host: str,
) -> None:
    monkeypatch.setattr(
        security.socket, "getaddrinfo", lambda *_args, **_kwargs: [_address("127.0.0.1")],
    )

    with pytest.raises(ValueError, match="Private or reserved"):
        security._resolve_public_https_target(f"https://{host}/image.png")


def test_download_pins_the_single_validated_dns_answer_and_refuses_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    public = _address("93.184.216.34")

    def changing_dns(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return [public] if calls == 1 else [_address("127.0.0.1")]

    class RedirectResponse:
        status = 302
        length = 0

        @staticmethod
        def getheader(name: str, default: str = "") -> str:
            return "image/png" if name == "Content-Type" else default

        @staticmethod
        def read(_size: int) -> bytes:
            raise AssertionError("redirect responses must not be read or followed")

    observed: dict[str, object] = {}

    class FakeConnection:
        def __init__(self, host, addresses, _timeout) -> None:
            observed["host"] = host
            observed["addresses"] = addresses
            self.sock = None

        def request(self, method, target, *, headers):
            observed["request"] = (method, target, headers)

        @staticmethod
        def getresponse():
            return RedirectResponse()

        @staticmethod
        def close() -> None:
            pass

    monkeypatch.setattr(security.socket, "getaddrinfo", changing_dns)
    monkeypatch.setattr(security, "_PinnedHTTPSConnection", FakeConnection)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="unsuccessful status"):
            await security.download_public_https_image(
                "https://images.example/image.png", max_bytes=1024,
            )

    asyncio.run(scenario())

    assert calls == 1
    assert observed["host"] == "images.example"
    assert observed["addresses"] == (public,)
    method, target, headers = observed["request"]
    assert (method, target) == ("GET", "/image.png")
    assert headers["Host"] == "images.example"
    assert "Authorization" not in headers


def test_pinned_connection_uses_validated_socket_address_and_original_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeSocket:
        def settimeout(self, _timeout) -> None:
            pass

        def connect(self, sockaddr) -> None:
            observed["sockaddr"] = sockaddr

        def close(self) -> None:
            pass

    class FakeTLSContext:
        verify_mode = security.ssl.CERT_REQUIRED
        check_hostname = True

        @staticmethod
        def set_alpn_protocols(_protocols) -> None:
            pass

        @staticmethod
        def wrap_socket(raw_socket, *, server_hostname):
            observed["server_hostname"] = server_hostname
            return raw_socket

    monkeypatch.setattr(security.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(security.ssl, "create_default_context", FakeTLSContext)
    monkeypatch.setattr(
        security.socket, "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("DNS was repeated")),
    )

    connection = security._PinnedHTTPSConnection(
        "images.example", (_address("93.184.216.34"),), 5.0,
    )
    connection.connect()

    assert observed["sockaddr"] == ("93.184.216.34", 443)
    assert observed["server_hostname"] == "images.example"


def test_successful_image_response_is_bounded_and_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        security.socket, "getaddrinfo", lambda *_args, **_kwargs: [_address("93.184.216.34")],
    )

    class ImageResponse:
        status = 200
        length = 4

        @staticmethod
        def getheader(name: str, default: str = "") -> str:
            return "image/png; charset=binary" if name == "Content-Type" else default

        @staticmethod
        def read1(size: int) -> bytes:
            assert size == 4
            return b"PNG!"

    class FakeConnection:
        def __init__(self, *_args) -> None:
            self.sock = None

        @staticmethod
        def request(*_args, **_kwargs) -> None:
            pass

        @staticmethod
        def getresponse():
            return ImageResponse()

        @staticmethod
        def close() -> None:
            pass

    monkeypatch.setattr(security, "_PinnedHTTPSConnection", FakeConnection)

    result = asyncio.run(security.download_public_https_image(
        "https://images.example/image.png", max_bytes=4,
    ))

    assert result == (b"PNG!", "image/png")
