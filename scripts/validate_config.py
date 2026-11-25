"""Validate repository configuration without contacting external services.

Checks the documented environment surface and the validated Settings loader.
Exits non-zero on a real mismatch so CI fails instead of silently drifting.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SOURCE = ROOT / "src" / "enan_ultra" / "config.py"
ENV_EXAMPLE = ROOT / ".env.example"

_TEST_ENVIRONMENT = {
    "TELEGRAM_BOT_TOKEN": "123456:configuration-validation-token-long-enough",
    "ADMIN_CHAT_ID": "123456",
    "ADMIN_USER_IDS": "123456",
    "OPENROUTER_API_KEY": "configuration-validation-key",
    "APP_DATA_KEY": "k" * 64,
    "DASHBOARD_AUTH_TOKEN": "configuration-validation-dashboard-token-123456",
}


def _documented_keys() -> tuple[set[str], list[str]]:
    keys: set[str] = set()
    duplicates: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in keys:
            duplicates.append(key)
        keys.add(key)
    return keys, sorted(set(duplicates))


def _loader_keys() -> set[str]:
    return set(re.findall(r'_value\(\s*"([A-Z0-9_]+)"', CONFIG_SOURCE.read_text(encoding="utf-8")))


def _load_settings(monkeypatch: dict[str, str]):
    from enan_ultra.config import Settings

    previous = dict(os.environ)
    try:
        for name in _TEST_ENVIRONMENT:
            os.environ.pop(name, None)
        os.environ.update(_TEST_ENVIRONMENT)
        os.environ.update(monkeypatch)
        # Disable implicit dotenv discovery: python-dotenv walks up from the
        # config module, so a repository .env is found even from another cwd.
        return Settings.load(dotenv_path=None)
    finally:
        os.environ.clear()
        os.environ.update(previous)


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    if not ENV_EXAMPLE.exists():
        errors.append(".env.example is missing")
        return _report(errors, warnings)

    documented, duplicates = _documented_keys()
    for key in duplicates:
        errors.append(f".env.example declares {key} more than once")
    for key in sorted(_loader_keys() - documented):
        errors.append(f".env.example does not document configuration key {key}")

    try:
        settings = _load_settings({})
    except Exception as exc:  # noqa: BLE001 - report the configuration failure
        errors.append(f"Settings.load() failed with documented defaults: {type(exc).__name__}")
    else:
        if settings.access_mode not in {"public", "allowlist"}:
            errors.append(f"unexpected default ACCESS_MODE {settings.access_mode!r}")

    try:
        _load_settings({"ACCESS_MODE": "enabled"})
    except ValueError:
        pass
    except Exception as exc:  # noqa: BLE001
        errors.append(f"invalid ACCESS_MODE raised {type(exc).__name__} instead of ValueError")
    else:
        errors.append("invalid ACCESS_MODE was accepted")

    try:
        _load_settings({"ACCESS_MODE": "allowlist", "ALLOWED_USER_IDS": ""})
    except ValueError:
        pass
    except Exception as exc:  # noqa: BLE001
        errors.append(f"allowlist without users raised {type(exc).__name__} instead of ValueError")
    else:
        errors.append("ALLOWLIST mode without ALLOWED_USER_IDS was accepted")

    if not (ROOT / "deploy" / "telegram-ai.service").exists():
        warnings.append("deploy/telegram-ai.service is missing")

    return _report(errors, warnings)


def _report(errors: list[str], warnings: list[str]) -> int:
    for message in warnings:
        print(f"warning: {message}")
    for message in errors:
        print(f"error: {message}")
    if errors:
        return 1
    print("configuration validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
