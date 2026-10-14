"""Small security primitives shared by Telegram and provider integrations."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


def require_public_https_url(url: str) -> str:
    """Syntactically validate provider returned image URLs (DNS checks are async)."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS URLs are accepted")
    if parsed.port not in (None, 443):
        raise ValueError("Unexpected HTTPS port")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address and (not address.is_global or address.is_multicast or address.is_reserved):
        raise ValueError("Private or reserved URL targets are blocked")
    return url


async def validate_public_https_url(url: str) -> str:
    """Reject private, loopback, link-local, and non-global DNS destinations."""
    require_public_https_url(url)
    host = urlsplit(url).hostname
    assert host is not None
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM), timeout=5
        )
    except (OSError, TimeoutError) as exc:
        raise ValueError("Image host could not be resolved") from exc
    if not records:
        raise ValueError("Image host has no address")
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError("Private or reserved DNS targets are blocked")
    return url


def is_admin(settings: object, user_id: int) -> bool:
    return user_id in settings.effective_admin_user_ids


def is_authorized(settings: object, user_id: int) -> bool:
    return is_admin(settings, user_id) or settings.access_mode == "public" or user_id in settings.allowed_user_ids


def safe_filename(filename: str, default: str = "upload.bin") -> str:
    from pathlib import PurePath

    cleaned = PurePath(filename.replace("\\", "/")).name
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in "\r\n\x00")
    return cleaned[:180] or default
