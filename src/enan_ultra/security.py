"""Small security primitives shared by Telegram and provider integrations."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import socket
import ssl
import time
from typing import Any
from urllib.parse import quote, urlsplit

_CONNECTION_TIMEOUT_SECONDS = 10.0
_TOTAL_DOWNLOAD_TIMEOUT_SECONDS = 35.0
_CHUNK_SIZE = 64 * 1024

_AddressInfo = tuple[int, int, int, str, tuple[Any, ...]]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """One-shot HTTPS connection to pre-resolved IPs with original-host TLS checks."""

    def __init__(
        self, hostname: str, addresses: tuple[_AddressInfo, ...], timeout: float,
    ) -> None:
        self._resolved_addresses = addresses
        self._verified_context = ssl.create_default_context()
        self._verified_context.set_alpn_protocols(["http/1.1"])
        super().__init__(hostname, 443, timeout=timeout, context=self._verified_context)

    def connect(self) -> None:
        if self._tunnel_host:
            raise OSError("HTTPS proxies are not permitted for external image requests")
        failures: list[OSError] = []
        for family, socktype, protocol, _canonname, sockaddr in self._resolved_addresses:
            raw_socket = socket.socket(family, socktype, protocol)
            try:
                raw_socket.settimeout(self.timeout)
                # sockaddr came from the single validated DNS lookup; never resolve the
                # hostname again between validation and the TCP connection.
                raw_socket.connect(sockaddr)
                self.sock = self._verified_context.wrap_socket(
                    raw_socket, server_hostname=self.host,
                )
                return
            except OSError as exc:
                raw_socket.close()
                failures.append(exc)
        if failures:
            raise failures[-1]
        raise OSError("No validated address is available for the image host")


def _is_public_address(value: str) -> bool:
    if "%" in value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_reserved


def _parse_public_https_url(url: str) -> tuple[str, int, str, str]:
    if (
        not isinstance(url, str) or not url or len(url) > 8192
        or any(ord(char) < 32 or ord(char) == 127 for char in url)
        or "\\" in url
    ):
        raise ValueError("Only public HTTPS URLs are accepted")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Only public HTTPS URLs are accepted") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS URLs are accepted")
    if port not in (None, 443):
        raise ValueError("Unexpected HTTPS port")
    host = parsed.hostname
    if "%" in host:
        raise ValueError("Scoped IP addresses are not accepted")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Image host is malformed") from exc
        if not host or any(not label for label in host.rstrip(".").split(".")):
            raise ValueError("Image host is malformed")
    else:
        if not _is_public_address(str(literal)):
            raise ValueError("Private or reserved URL targets are blocked")
        host = str(literal)

    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    request_target = path
    if parsed.query:
        request_target += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    host_header = f"[{host}]" if ":" in host else host
    if port is not None:
        host_header += f":{port}"
    return host, 443, request_target, host_header


def _resolve_public_https_target(
    url: str,
) -> tuple[str, str, str, tuple[_AddressInfo, ...]]:
    host, port, request_target, host_header = _parse_public_https_url(url)
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Image host could not be resolved") from exc
    if not records:
        raise ValueError("Image host has no address")
    for record in records:
        try:
            address = record[4][0]
        except (IndexError, TypeError) as exc:
            raise ValueError("Image host returned an invalid address") from exc
        if not _is_public_address(address):
            raise ValueError("Private or reserved DNS targets are blocked")
    return host, request_target, host_header, tuple(records)


def _download_public_https_image(
    url: str, max_bytes: int, timeout_seconds: float,
) -> tuple[bytes, str]:
    host, request_target, host_header, addresses = _resolve_public_https_target(url)
    connection = _PinnedHTTPSConnection(host, addresses, timeout_seconds)
    try:
        connection.request(
            "GET", request_target,
            headers={
                "Host": host_header, "Accept": "image/*",
                "Accept-Encoding": "identity", "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("External image host returned an unsuccessful status")
        media_type = (response.getheader("Content-Type", "") or "").split(";", 1)[0].strip().lower()
        if media_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("External image host returned an unsupported content type")
        if (response.getheader("Content-Encoding", "identity") or "identity").lower() not in {
            "", "identity",
        }:
            raise ValueError("External image host returned an unsupported content encoding")
        expected_length = response.length
        if expected_length is not None and expected_length > max_bytes:
            raise ValueError("Generated image exceeds MAX_IMAGE_BYTES")
        output = bytearray()
        deadline = time.monotonic() + timeout_seconds
        while len(output) <= max_bytes:
            if expected_length is not None and len(output) >= expected_length:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("External image response exceeded its time limit")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            byte_limit = (
                expected_length - len(output)
                if expected_length is not None else max_bytes + 1 - len(output)
            )
            chunk = response.read1(min(_CHUNK_SIZE, byte_limit))
            if not chunk:
                break
            output.extend(chunk)
        if not output or len(output) > max_bytes:
            raise ValueError("External image response is empty or exceeds MAX_IMAGE_BYTES")
        if expected_length is not None and len(output) != expected_length:
            raise ValueError("External image response ended before its declared size")
        return bytes(output), media_type
    finally:
        connection.close()


def require_public_https_url(url: str) -> str:
    """Syntactically validate provider returned image URLs (DNS checks are async)."""
    _parse_public_https_url(url)
    return url


async def validate_public_https_url(url: str) -> str:
    """Resolve and validate an image host; this preflight alone must not authorize a fetch."""
    try:
        await asyncio.wait_for(asyncio.to_thread(_resolve_public_https_target, url), timeout=5)
    except TimeoutError as exc:
        raise ValueError("Image host resolution timed out") from exc
    return url


async def download_public_https_image(
    url: str, *, max_bytes: int, timeout_seconds: float = _CONNECTION_TIMEOUT_SECONDS,
) -> tuple[bytes, str]:
    """Fetch one public image with connection-time DNS pinning and normal TLS verification."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_download_public_https_image, url, max_bytes, timeout_seconds),
            timeout=_TOTAL_DOWNLOAD_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise ValueError("External image request timed out") from exc


def is_admin(settings: object, user_id: int) -> bool:
    return user_id in settings.effective_admin_user_ids


def is_authorized(settings: object, user_id: int) -> bool:
    return is_admin(settings, user_id) or settings.access_mode == "public" or user_id in settings.allowed_user_ids


def safe_filename(filename: str, default: str = "upload.bin") -> str:
    from pathlib import PurePath

    cleaned = PurePath(filename.replace("\\", "/")).name
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in "\r\n\x00")
    cleaned = cleaned.strip()
    if cleaned.strip(".") == "":
        # "", ".", ".." and similar dot-only names must never be used as path components.
        return default
    return cleaned[:180] or default
