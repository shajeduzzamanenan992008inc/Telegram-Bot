"""Filename sanitising, SSRF-safe URL validation and access-control messages."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from enan_ultra.access_control import AccessControlService
from enan_ultra.security import require_public_https_url, safe_filename


@pytest.mark.parametrize("raw,expected", [
    ("report.pdf", "report.pdf"),
    ("../../etc/passwd", "passwd"),
    ("a/b\\c.txt", "c.txt"),
    ("x\x00y.txt", "xy.txt"),
    ("বাংলা নথি.docx", "বাংলা নথি.docx"),
    ("..", "upload.bin"),
    (".", "upload.bin"),
    ("...", "upload.bin"),
    ("   ", "upload.bin"),
    ("", "upload.bin"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_is_length_limited():
    assert len(safe_filename("a" * 500 + ".txt")) <= 180


@pytest.mark.parametrize("url", [
    "http://example.com/a.png",
    "https://127.0.0.1/a.png",
    "https://[::1]/a.png",
    "https://10.0.0.5/a.png",
    "https://169.254.169.254/latest/meta-data",
    "https://user:pass@example.com/a.png",
    "https://example.com:8443/a.png",
    "ftp://example.com/a.png",
    "file:///etc/passwd",
    "https:///nohost",
])
def test_non_public_or_non_https_urls_are_rejected(url):
    with pytest.raises(ValueError):
        require_public_https_url(url)


def test_public_https_url_is_accepted():
    assert require_public_https_url("https://example.com/a.png").startswith("https://example.com")


def test_private_mode_notice_is_plain_text():
    """Regression: the notice was sent without a parse mode but contained literal ** markers."""
    sent = []

    async def reply_text(text, **kwargs):
        sent.append((text, kwargs))

    class Service(AccessControlService):
        def __init__(self):
            pass

        async def is_access_allowed(self, user_id):
            return False, "private_mode"

    update = SimpleNamespace(effective_user=SimpleNamespace(id=5), effective_message=SimpleNamespace(reply_text=reply_text))
    assert asyncio.run(Service().enforce_private_mode_for_update(update)) is False
    text, kwargs = sent[0]
    assert "Private Mode" in text
    assert "**" not in text and "parse_mode" not in kwargs
