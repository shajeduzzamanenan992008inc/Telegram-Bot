"""Generate deployment-only secrets and validate mandatory credentials."""

from __future__ import annotations

import secrets
import re
import sys
from pathlib import Path


def read_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def provider_keys(values: dict[str, str], plural_name: str, singular_name: str) -> tuple[str, ...]:
    keys = tuple(part.strip() for part in values.get(plural_name, "").split(",") if part.strip())
    if keys:
        return keys
    singular = values.get(singular_name, "").strip()
    return (singular,) if singular else ()


def main() -> int:
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    template_path = Path(__file__).resolve().parent.parent / ".env.example"
    existing_names = set(read_values(path))
    missing_lines = []
    for line in template_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if name not in existing_names:
            missing_lines.append(line)
            existing_names.add(name)
    if missing_lines:
        text = text.rstrip() + "\n\n# Added from the current Enan Ultra configuration template\n" + "\n".join(missing_lines) + "\n"
    generated = {
        "APP_DATA_KEY": secrets.token_urlsafe(48),
        "DASHBOARD_AUTH_TOKEN": secrets.token_urlsafe(48),
    }
    for name, value in generated.items():
        marker = f"{name}=replace-with-generated-secret"
        if marker in text:
            text = text.replace(marker, f"{name}={value}")
    if "DATABASE_PATH=data/telegram_ai.sqlite3" in text:
        text = text.replace("DATABASE_PATH=data/telegram_ai.sqlite3", "DATABASE_PATH=/var/lib/telegram-ai/assistant.sqlite3")
    path.write_text(text, encoding="utf-8")
    values = read_values(path)
    required = ("TELEGRAM_BOT_TOKEN", "ADMIN_CHAT_ID", "ADMIN_USER_IDS", "APP_DATA_KEY", "DASHBOARD_AUTH_TOKEN")
    missing = [name for name in required if not values.get(name) or values[name].startswith("replace-")]
    if values.get("TELEGRAM_BOT_TOKEN") and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", values["TELEGRAM_BOT_TOKEN"]):
        missing.append("a valid TELEGRAM_BOT_TOKEN")
    if values.get("ADMIN_CHAT_ID") and not re.fullmatch(r"-?[0-9]+", values["ADMIN_CHAT_ID"]):
        missing.append("a numeric ADMIN_CHAT_ID")
    if values.get("ADMIN_USER_IDS"):
        admin_ids = [item.strip() for item in values["ADMIN_USER_IDS"].split(",") if item.strip()]
        if not admin_ids or any(not item.isdigit() or int(item) <= 0 for item in admin_ids):
            missing.append("valid numeric ADMIN_USER_IDS")
    if values.get("ACCESS_MODE", "public").lower() == "allowlist" and not values.get("ALLOWED_USER_IDS"):
        missing.append("ALLOWED_USER_IDS for ACCESS_MODE=allowlist")
    provider_settings = (
        ("OPENAI_API_KEYS", "OPENAI_API_KEY", "OPENAI_MODEL"),
        ("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
        ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GEMINI_MODEL"),
    )
    keyed_providers = (
        ("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY", ""),
        *provider_settings,
    )
    for plural, singular, model in keyed_providers:
        keys = provider_keys(values, plural, singular)
        if any(key.startswith("replace-") for key in keys):
            missing.append(plural)
        if keys and model and not values.get(model):
            missing.append(model)
    configured_provider = False
    for plural, singular, model in keyed_providers:
        keys = provider_keys(values, plural, singular)
        if keys and all(not key.startswith("replace-") for key in keys) and (not model or values.get(model)):
            configured_provider = True
            break
    configured_provider = configured_provider or bool(
        values.get("OLLAMA_BASE_URL") and values.get("OLLAMA_MODEL")
    )
    if not configured_provider:
        missing.append("at least one provider key/endpoint with its model ID")
    if missing:
        print("Configure these values in /etc/telegram-ai/bot.env: " + ", ".join(dict.fromkeys(missing)))
        return 2
    print("Deployment environment has required credentials and generated data/dashboard secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
