"""Regression tests for dotenv isolation in configuration validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from enan_ultra import config
from enan_ultra.config import Settings

_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:configuration-isolation-token-long-enough",
    "ADMIN_CHAT_ID": "123456",
    "ADMIN_USER_IDS": "123456",
    "OPENROUTER_API_KEY": "configuration-isolation-key",
    "APP_DATA_KEY": "k" * 64,
    "DASHBOARD_AUTH_TOKEN": "configuration-isolation-dashboard-token-123456",
}


def _set_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    for name in ("ACCESS_MODE", "ALLOWED_USER_IDS"):
        monkeypatch.delenv(name, raising=False)


def test_disabling_dotenv_ignores_implicit_discovery_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_environment(monkeypatch)
    calls: list[tuple[object, ...]] = []

    def poisoned_dotenv(*args):
        calls.append(args)
        return {"ACCESS_MODE": "enabled"} if not args else {}

    monkeypatch.setattr(config, "dotenv_values", poisoned_dotenv)

    settings = Settings.load(dotenv_path=None)

    assert settings.access_mode == "public"
    assert calls == []


def test_explicit_dotenv_path_is_used_instead_of_implicit_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_environment(monkeypatch)
    dotenv_file = tmp_path / "isolated.env"
    dotenv_file.write_text(
        "ACCESS_MODE=allowlist\nALLOWED_USER_IDS=777\n", encoding="utf-8",
    )
    calls: list[tuple[object, ...]] = []

    import dotenv

    def reader(*args):
        calls.append(args)
        if not args:
            return {"ACCESS_MODE": "enabled"}
        return dotenv.dotenv_values(*args)

    monkeypatch.setattr(config, "dotenv_values", reader)

    settings = Settings.load(dotenv_path=dotenv_file)

    assert settings.access_mode == "allowlist"
    assert settings.allowed_user_ids == frozenset({777})
    assert calls and all(args for args in calls)


def test_configuration_validator_explicitly_disables_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import validate_config

    observed: dict[str, object] = {}

    @classmethod
    def fake_load(cls, **kwargs):
        observed.update(kwargs)
        return object()

    monkeypatch.setattr(Settings, "load", fake_load)
    validate_config._load_settings({})
    assert observed == {"dotenv_path": None}
