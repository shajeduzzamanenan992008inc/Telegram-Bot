"""Validated environment configuration."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

def _value(name: str, default: str = "") -> str:
    from_file = dotenv_values().get(name)
    return os.environ.get(name, str(from_file) if from_file is not None else default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = _value(name, str(default).lower()).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(name: str, default: int, minimum: int = 0, maximum: int = 2_000_000_000) -> int:
    try:
        value = int(_value(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(_value(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum or value > 10_000_000:
        raise ValueError(f"{name} must be a finite number between {minimum} and 10000000")
    return value


def _csv(name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _value(name).split(",") if part.strip())


def _ids(name: str) -> frozenset[int]:
    try:
        values = frozenset(int(part) for part in _csv(name))
        if any(value <= 0 for value in values):
            raise ValueError
        return values
    except ValueError as exc:
        raise ValueError(f"{name} must contain comma-separated numeric IDs") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    bot_name: str
    display_name: str
    persona_profile: str
    telegram_token: str
    admin_chat_id: int
    admin_user_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    access_mode: str
    openrouter_key: str
    openrouter_base_url: str
    default_model: str
    fallback_models: tuple[str, ...]
    provider_order: tuple[str, ...]
    openai_key: str
    openai_base_url: str
    openai_model: str
    anthropic_key: str
    anthropic_base_url: str
    anthropic_model: str
    gemini_key: str
    gemini_base_url: str
    gemini_model: str
    ollama_base_url: str
    ollama_model: str
    provider_request_reserve_usd: float
    agent_enabled_by_default: bool
    agent_max_steps: int
    agent_max_workers: int
    agent_max_cost_usd: float
    stt_model: str
    tts_model: str
    tts_voice: str
    image_model: str
    trigger_words: tuple[str, ...]
    database_path: Path
    history_messages: int
    max_input_chars: int
    max_output_chars: int
    max_upload_bytes: int
    max_image_bytes: int
    max_image_pixels: int
    max_archive_expanded_bytes: int
    max_archive_files: int
    max_archive_depth: int
    max_document_pages: int
    max_video_seconds: int
    max_video_frames: int
    max_audio_seconds: int
    cooldown_seconds: float
    user_daily_request_limit: int
    max_daily_cost_usd: float
    image_cost_reserve_usd: float
    tts_cost_reserve_usd: float
    web_search_cost_reserve_usd: float
    document_retention_hours: int
    max_pending_reminders: int
    max_document_chunks: int
    memory_enabled_by_default: bool
    kb_enabled: bool
    app_data_key: str
    kb_max_documents: int
    kb_max_bytes: int
    remote_file_fallback: bool
    web_search_enabled: bool
    image_generation_enabled: bool
    voice_input_enabled: bool
    voice_output_mode: str
    sticker_enabled: bool
    sticker_file_ids: tuple[str, ...]
    media_style: str
    admin_alerts: bool
    log_level: str
    local_bot_api_base_url: str
    local_bot_api_file_url: str
    dashboard_enabled: bool
    dashboard_host: str
    dashboard_port: int
    dashboard_auth_token: str
    ha_base_url: str
    ha_token: str
    ha_allowed_entities: tuple[str, ...]
    ha_allowed_services: tuple[str, ...]
    sandbox_enabled: bool
    sandbox_image: str
    sandbox_timeout_seconds: int
    sandbox_memory_mb: int
    sandbox_cpu_count: float
    sandbox_max_files: int
    sandbox_max_expanded_bytes: int

    @property
    def effective_admin_user_ids(self) -> frozenset[int]:
        return self.admin_user_ids

    @classmethod
    def load(cls) -> "Settings":
        token = _value("TELEGRAM_BOT_TOKEN")
        if not token or token.startswith("replace-"):
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            raise ValueError("TELEGRAM_BOT_TOKEN has an invalid format")
        api_key = _value("OPENROUTER_API_KEY")
        openai_key = _value("OPENAI_API_KEY")
        anthropic_key = _value("ANTHROPIC_API_KEY")
        gemini_key = _value("GEMINI_API_KEY")
        ollama_url = _value("OLLAMA_BASE_URL").rstrip("/")
        for name, value in (("OPENROUTER_API_KEY", api_key), ("OPENAI_API_KEY", openai_key),
                            ("ANTHROPIC_API_KEY", anthropic_key), ("GEMINI_API_KEY", gemini_key)):
            if value.startswith("replace-"):
                raise ValueError(f"{name} must contain a real key or be left blank")
        provider_order = tuple(
            name for name in _csv("AI_PROVIDER_ORDER")
            if name in {"openrouter", "openai", "anthropic", "gemini", "ollama"}
        ) or ("openrouter", "openai", "anthropic", "gemini", "ollama")
        provider_models = {
            "openrouter": bool(api_key),
            "openai": bool(openai_key and _value("OPENAI_MODEL")),
            "anthropic": bool(anthropic_key and _value("ANTHROPIC_MODEL")),
            "gemini": bool(gemini_key and _value("GEMINI_MODEL")),
            "ollama": bool(ollama_url and _value("OLLAMA_MODEL")),
        }
        if not any(provider_models.values()):
            raise ValueError("Configure at least one provider key/endpoint and its model ID")
        if openai_key and not _value("OPENAI_MODEL"):
            raise ValueError("OPENAI_MODEL is required when OPENAI_API_KEY is configured")
        if anthropic_key and not _value("ANTHROPIC_MODEL"):
            raise ValueError("ANTHROPIC_MODEL is required when ANTHROPIC_API_KEY is configured")
        if gemini_key and not _value("GEMINI_MODEL"):
            raise ValueError("GEMINI_MODEL is required when GEMINI_API_KEY is configured")
        if ollama_url and not _value("OLLAMA_MODEL"):
            raise ValueError("OLLAMA_MODEL is required when OLLAMA_BASE_URL is configured")
        try:
            admin_chat_id = int(_value("ADMIN_CHAT_ID"))
        except ValueError as exc:
            raise ValueError("ADMIN_CHAT_ID must be a numeric Telegram chat ID") from exc
        if admin_chat_id == 0:
            raise ValueError("ADMIN_CHAT_ID must not be zero")

        access_mode = _value("ACCESS_MODE", "public").lower()
        if access_mode not in {"public", "allowlist"}:
            raise ValueError("ACCESS_MODE must be public or allowlist")
        voice_output_mode = _value("VOICE_OUTPUT_MODE", "on_voice_input").lower()
        if voice_output_mode not in {"off", "on_voice_input", "always"}:
            raise ValueError("VOICE_OUTPUT_MODE must be off, on_voice_input, or always")
        admin_user_ids = _ids("ADMIN_USER_IDS")
        if not admin_user_ids:
            raise ValueError("ADMIN_USER_IDS must contain at least one Telegram user ID")

        settings = cls(
            bot_name=_value("BOT_NAME", "Enan Ultra"),
            display_name=_value("BOT_DISPLAY_NAME", "Enan Ultra AI"),
            persona_profile=_value("AI_PERSONA_PROFILE", "GPT-6 Astra"),
            telegram_token=token,
            admin_chat_id=admin_chat_id,
            admin_user_ids=admin_user_ids,
            allowed_user_ids=_ids("ALLOWED_USER_IDS"),
            access_mode=access_mode,
            openrouter_key=api_key,
            openrouter_base_url=_value("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            default_model=_value("DEFAULT_AI_MODEL", "openrouter/free"),
            fallback_models=_csv("FALLBACK_MODELS"),
            provider_order=provider_order,
            openai_key=openai_key,
            openai_base_url=_value("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_model=_value("OPENAI_MODEL"),
            anthropic_key=anthropic_key,
            anthropic_base_url=_value("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
            anthropic_model=_value("ANTHROPIC_MODEL"),
            gemini_key=gemini_key,
            gemini_base_url=_value("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
            gemini_model=_value("GEMINI_MODEL"),
            ollama_base_url=ollama_url,
            ollama_model=_value("OLLAMA_MODEL"),
            provider_request_reserve_usd=_float("PROVIDER_REQUEST_RESERVE_USD", 0.10, 0),
            agent_enabled_by_default=_bool("AGENTS_ENABLED_BY_DEFAULT", True),
            agent_max_steps=_int("AGENT_MAX_STEPS", 4, 1, 12),
            agent_max_workers=_int("AGENT_MAX_WORKERS", 2, 1, 4),
            agent_max_cost_usd=_float("AGENT_MAX_COST_USD", 0.50, 0),
            stt_model=_value("STT_MODEL"),
            tts_model=_value("TTS_MODEL"),
            tts_voice=_value("TTS_VOICE", "alloy"),
            image_model=_value("IMAGE_MODEL"),
            trigger_words=tuple(word.casefold() for word in _csv("TRIGGER_WORDS")),
            database_path=Path(_value("DATABASE_PATH", "data/telegram_ai.sqlite3")),
            history_messages=_int("HISTORY_MESSAGES", 12, 2, 100),
            max_input_chars=_int("MAX_INPUT_CHARS", 12000, 100, 200_000),
            max_output_chars=_int("MAX_OUTPUT_CHARS", 24000, 1000, 100_000),
            max_upload_bytes=_int("MAX_UPLOAD_BYTES", 20 * 1024 * 1024, 1024, 100 * 1024 * 1024),
            max_image_bytes=_int("MAX_IMAGE_BYTES", 3 * 1024 * 1024, 1024, 20 * 1024 * 1024),
            max_image_pixels=_int("MAX_IMAGE_PIXELS", 40_000_000, 1_000_000, 80_000_000),
            max_archive_expanded_bytes=_int("MAX_ARCHIVE_EXPANDED_BYTES", 50 * 1024 * 1024, 1024, 500 * 1024 * 1024),
            max_archive_files=_int("MAX_ARCHIVE_FILES", 100, 1, 2000),
            max_archive_depth=_int("MAX_ARCHIVE_DEPTH", 2, 0, 8),
            max_document_pages=_int("MAX_DOCUMENT_PAGES", 100, 1, 2000),
            max_video_seconds=_int("MAX_VIDEO_SECONDS", 120, 1, 3600),
            max_video_frames=_int("MAX_VIDEO_FRAMES", 4, 1, 8),
            max_audio_seconds=_int("MAX_AUDIO_SECONDS", 600, 1, 3600),
            cooldown_seconds=_float("REQUEST_COOLDOWN_SECONDS", 1.5, 0),
            user_daily_request_limit=_int("USER_DAILY_REQUEST_LIMIT", 40, 1, 10000),
            max_daily_cost_usd=_float("MAX_DAILY_COST_USD", 5.0, 0),
            image_cost_reserve_usd=_float("IMAGE_COST_RESERVE_USD", 0.10, 0),
            tts_cost_reserve_usd=_float("TTS_COST_RESERVE_USD", 0.03, 0),
            web_search_cost_reserve_usd=_float("WEB_SEARCH_COST_RESERVE_USD", 0.01, 0),
            document_retention_hours=_int("DOCUMENT_RETENTION_HOURS", 24, 1, 24 * 365),
            max_pending_reminders=_int("MAX_PENDING_REMINDERS", 20, 1, 500),
            max_document_chunks=_int("MAX_DOCUMENT_CHUNKS", 200, 1, 5000),
            memory_enabled_by_default=_bool("MEMORY_ENABLED_BY_DEFAULT", False),
            kb_enabled=_bool("PERSONAL_KB_ENABLED", True),
            app_data_key=_value("APP_DATA_KEY"),
            kb_max_documents=_int("PERSONAL_KB_MAX_DOCUMENTS", 50, 1, 10_000),
            kb_max_bytes=_int("PERSONAL_KB_MAX_BYTES", 100 * 1024 * 1024, 1024, 10_000_000_000),
            remote_file_fallback=_bool("REMOTE_FILE_FALLBACK", True),
            web_search_enabled=_bool("WEB_SEARCH_ENABLED", True),
            image_generation_enabled=_bool("IMAGE_GENERATION_ENABLED", True),
            voice_input_enabled=_bool("VOICE_INPUT_ENABLED", True),
            voice_output_mode=voice_output_mode,
            sticker_enabled=_bool("STICKER_ENABLED", True),
            sticker_file_ids=_csv("STICKER_FILE_IDS"),
            media_style=_value("MEDIA_STYLE", "balanced").lower(),
            admin_alerts=_bool("ADMIN_ALERTS", True),
            log_level=_value("LOG_LEVEL", "INFO").upper(),
            local_bot_api_base_url=_value("LOCAL_BOT_API_BASE_URL"),
            local_bot_api_file_url=_value("LOCAL_BOT_API_FILE_URL"),
            dashboard_enabled=_bool("DASHBOARD_ENABLED", True),
            dashboard_host=_value("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=_int("DASHBOARD_PORT", 8787, 1024, 65535),
            dashboard_auth_token=_value("DASHBOARD_AUTH_TOKEN"),
            ha_base_url=_value("HA_BASE_URL").rstrip("/"),
            ha_token=_value("HA_TOKEN"),
            ha_allowed_entities=_csv("HA_ALLOWED_ENTITIES"),
            ha_allowed_services=_csv("HA_ALLOWED_SERVICES"),
            sandbox_enabled=_bool("CODE_SANDBOX_ENABLED", False),
            sandbox_image=_value("CODE_SANDBOX_IMAGE", "enan-ultra-python-sandbox:py3.15.0-pytest9.1.1"),
            sandbox_timeout_seconds=_int("CODE_SANDBOX_TIMEOUT_SECONDS", 30, 5, 180),
            sandbox_memory_mb=_int("CODE_SANDBOX_MEMORY_MB", 512, 128, 4096),
            sandbox_cpu_count=_float("CODE_SANDBOX_CPU_COUNT", 1.0, 0.1),
            sandbox_max_files=_int("CODE_SANDBOX_MAX_FILES", 100, 1, 1000),
            sandbox_max_expanded_bytes=_int("CODE_SANDBOX_MAX_EXPANDED_BYTES", 50 * 1024 * 1024, 1024, 500 * 1024 * 1024),
        )
        if settings.access_mode == "allowlist" and not settings.allowed_user_ids:
            raise ValueError("ALLOWED_USER_IDS is required when ACCESS_MODE=allowlist")
        parsed_api_url = urlsplit(settings.openrouter_base_url)
        if parsed_api_url.scheme != "https" or not parsed_api_url.hostname:
            raise ValueError("OPENROUTER_BASE_URL must be a valid HTTPS URL")
        if not settings.bot_name or not settings.display_name or not settings.persona_profile or not settings.default_model:
            raise ValueError("BOT_NAME, BOT_DISPLAY_NAME, AI_PERSONA_PROFILE, and DEFAULT_AI_MODEL must not be empty")
        if settings.dashboard_enabled and (
            len(settings.dashboard_auth_token) < 32
            or settings.dashboard_auth_token.startswith("replace-")
        ):
            raise ValueError("DASHBOARD_AUTH_TOKEN must be a generated secret of at least 32 characters")
        if settings.dashboard_host != "127.0.0.1":
            raise ValueError("DASHBOARD_HOST must be 127.0.0.1; access it through an SSH tunnel")
        if len(settings.app_data_key) < 32 or settings.app_data_key.startswith("replace-"):
            raise ValueError("APP_DATA_KEY must be a generated secret of at least 32 characters")
        if bool(settings.ha_base_url) != bool(settings.ha_token):
            raise ValueError("HA_BASE_URL and HA_TOKEN must be configured together")
        if settings.ha_base_url:
            ha_url = urlsplit(settings.ha_base_url)
            if (ha_url.scheme not in {"http", "https"} or not ha_url.hostname or ha_url.username
                    or ha_url.password or ha_url.query or ha_url.fragment):
                raise ValueError("HA_BASE_URL must be an HTTP(S) URL without embedded credentials")
            if ha_url.scheme == "http" and ha_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("HA_BASE_URL must use HTTPS unless Home Assistant is on this host")
            if any(not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", item) for item in settings.ha_allowed_entities):
                raise ValueError("HA_ALLOWED_ENTITIES must contain exact domain.entity IDs")
            if any(not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", item) for item in settings.ha_allowed_services):
                raise ValueError("HA_ALLOWED_SERVICES must contain exact domain.service names")
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", settings.telegram_token),
            ("OPENROUTER_API_KEY", settings.openrouter_key),
            ("OPENAI_API_KEY", settings.openai_key),
            ("ANTHROPIC_API_KEY", settings.anthropic_key),
            ("GEMINI_API_KEY", settings.gemini_key),
            ("HA_TOKEN", settings.ha_token),
            ("APP_DATA_KEY", settings.app_data_key),
            ("DASHBOARD_AUTH_TOKEN", settings.dashboard_auth_token),
        ):
            if any(char in value for char in "\r\n"):
                raise ValueError(f"{name} must be a single-line value")
        for name, value in (
            ("OPENROUTER_BASE_URL", settings.openrouter_base_url),
            ("OPENAI_BASE_URL", settings.openai_base_url),
            ("ANTHROPIC_BASE_URL", settings.anthropic_base_url),
            ("GEMINI_BASE_URL", settings.gemini_base_url),
        ):
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError(f"{name} must be a valid HTTPS URL")
        if settings.ollama_base_url:
            local_url = urlsplit(settings.ollama_base_url)
            if (not local_url.hostname or local_url.scheme not in {"http", "https"} or local_url.username
                    or local_url.password or local_url.query or local_url.fragment):
                raise ValueError("OLLAMA_BASE_URL must be a valid HTTP(S) URL")
            if local_url.scheme == "http" and local_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote OLLAMA_BASE_URL must use HTTPS")
        if settings.media_style not in {"balanced", "minimal", "expressive"}:
            raise ValueError("MEDIA_STYLE must be balanced, minimal, or expressive")
        if settings.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        if not settings.local_bot_api_base_url and settings.max_upload_bytes > 20 * 1024 * 1024:
            raise ValueError("MAX_UPLOAD_BYTES above 20 MB requires a configured local Bot API server")
        return settings
