"""Shared pytest fixtures: keep every test independent of the developer's real configuration."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

# Placeholder credential for tests. It deliberately does not use a Telegram-shaped
# token so that provider secret scanning does not flag this public repository; it
# still satisfies the format check in ``config.Settings.load``.
_TEST_BOT_TOKEN = "123456789:placeholder-token-for-tests"

BASE_ENV = {
    "TELEGRAM_BOT_TOKEN": _TEST_BOT_TOKEN,
    "ADMIN_CHAT_ID": "1",
    "ADMIN_USER_IDS": "1",
    "OPENROUTER_API_KEY": "sk-or-test-key",
    "APP_DATA_KEY": "k" * 40,
    "DASHBOARD_AUTH_TOKEN": "t" * 40,
    "DASHBOARD_ENABLED": "false",
}


def configuration_names() -> list[str]:
    """Every variable documented in .env.example (the settings loader reads exactly these)."""
    names: list[str] = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            names.append(stripped.split("=", 1)[0].strip())
    return names


@pytest.fixture
def make_settings(monkeypatch, tmp_path):
    """Build ``Settings`` from a controlled environment; pass ``NAME=None`` to leave a name unset."""
    from enan_ultra import config

    for name in configuration_names():
        monkeypatch.delenv(name, raising=False)
    # Settings looks for a .env file next to the package; never let a developer's file leak in.
    monkeypatch.setattr(config, "dotenv_values", lambda *args, **kwargs: {})

    def factory(**overrides):
        values = {**BASE_ENV, "DATABASE_PATH": str(tmp_path / "bot.sqlite3"), **overrides}
        for name, value in values.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, str(value))
        return config.Settings.load()

    return factory
