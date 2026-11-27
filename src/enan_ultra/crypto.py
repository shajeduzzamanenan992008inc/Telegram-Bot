"""Authenticated encryption for persisted user-controlled private content."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class DataCipher:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("APP_DATA_KEY must contain at least 32 characters")
        raw = hashlib.sha256(b"enan-ultra-data-v1\0" + secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(raw))

    def encrypt(self, value: str | bytes) -> bytes:
        data = value.encode("utf-8") if isinstance(value, str) else value
        return self._fernet.encrypt(data)

    def decrypt(self, value: bytes) -> bytes:
        try:
            return self._fernet.decrypt(value)
        except InvalidToken as exc:
            raise ValueError("Stored encrypted data cannot be opened; verify APP_DATA_KEY") from exc

    def decrypt_text(self, value: bytes) -> str:
        return self.decrypt(value).decode("utf-8")
