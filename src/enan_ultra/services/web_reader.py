"""Bounded HTML fetching and text extraction for the /read command."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from enan_ultra.security import fetch_public_https_document

MAX_HTML_BYTES = 2 * 1024 * 1024
_REMOVED_TAGS = (
    "script", "style", "header", "footer", "nav", "aside", "form", "noscript",
    "svg", "iframe", "button",
)


async def fetch_page_text(url: str) -> str:
    """Fetch a public HTML page without redirects and return its readable text."""
    parsed = _validate_url(url)
    # The fetch itself validates and pins every resolved address, so a hostname
    # cannot be re-resolved to a private address between validation and connect.
    body = await fetch_public_https_document(parsed.geturl(), max_bytes=MAX_HTML_BYTES)
    return extract_html_text(body)


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
