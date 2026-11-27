"""Bounded HTML fetching and text extraction for the /read command."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

MAX_HTML_BYTES = 2 * 1024 * 1024
_REMOVED_TAGS = (
    "script", "style", "header", "footer", "nav", "aside", "form", "noscript",
    "svg", "iframe", "button",
)


async def fetch_page_text(url: str) -> str:
    """Fetch a public HTML page without redirects and return its readable text."""
    parsed = _validate_url(url)
    await _validate_public_host(parsed.hostname or "")
    timeout = httpx.Timeout(connect=5, read=12, write=5, pool=5)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False,
        headers={"User-Agent": "EnanUltra-WebReader/1.0", "Accept": "text/html"},
    ) as client:
        async with client.stream("GET", str(parsed)) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Redirects are not followed for webpage reading")
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("The URL did not return an HTML page")
            length = response.headers.get("content-length")
            if length and int(length) > MAX_HTML_BYTES:
                raise ValueError("The webpage is larger than the 2 MiB reading limit")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_HTML_BYTES:
                    raise ValueError("The webpage is larger than the 2 MiB reading limit")

    return extract_html_text(bytes(body))


def extract_html_text(html: bytes | str) -> str:
    """Strip non-content regions and bound extracted text for model input."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(_REMOVED_TAGS):
        element.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = root.get_text(" ", strip=True)
    if not text:
        raise ValueError("No readable text was found on the webpage")
    return text[:3000]


def _validate_url(url: str):
    normalized = url.strip()
    if not normalized or len(normalized) > 2048:
        raise ValueError("Provide a valid webpage URL")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.fragment or parsed.port not in (None, 443)
    ):
        raise ValueError("Only public HTTPS webpage URLs on port 443 are supported")
    return parsed


async def _validate_public_host(hostname: str) -> None:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo, hostname, 443, type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("The webpage hostname could not be resolved") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
    else:
        addresses = {literal}
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Private or non-public webpage hosts are not allowed")
