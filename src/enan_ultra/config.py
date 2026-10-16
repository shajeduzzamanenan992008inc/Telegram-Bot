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


def _provider_keys(plural_name: str, singular_name: str) -> tuple[str, ...]:
    return _provider_keys_with_aliases(plural_name, singular_name)


def _provider_keys_with_aliases(
    plural_name: str, singular_name: str, aliases: tuple[str, ...] = (),
) -> tuple[str, ...]:
    source_name = plural_name
    raw = _value(plural_name)
    if not raw:
        for alias in aliases:
            raw = _value(alias)
            if raw:
                source_name = alias
                break
    if raw:
        parts = raw.split(",")
        if any(not part.strip() for part in parts):
            raise ValueError(f"{source_name} contains an empty credential entry")
        values = [part.strip() for part in parts]
    else:
        singular = _value(singular_name)
        values = [singular] if singular else []
        source_name = singular_name
    if any(any(char.isspace() for char in value) for value in values):
        raise ValueError(f"{source_name} contains malformed credentials")
    if len(values) > 10:
        raise ValueError(f"{source_name} supports at most 10 credentials")
    return tuple(dict.fromkeys(values))


_CLOUD_PROVIDER_NAMES = frozenset({"openrouter", "groq", "gemini", "cloudflare", "huggingface"})


def _provider_order() -> tuple[str, ...]:
    raw = _value("AI_PROVIDER_ORDER", "openrouter,groq,gemini,cloudflare,huggingface")
    parts = [item.strip().casefold() for item in raw.split(",")]
    if not raw or any(not item for item in parts):
        raise ValueError("AI_PROVIDER_ORDER must be a comma-separated provider list")
    if len(set(parts)) != len(parts) or any(item not in _CLOUD_PROVIDER_NAMES for item in parts):
        raise ValueError("AI_PROVIDER_ORDER may contain each supported cloud provider at most once")
    return tuple(parts)


def _model_allowlist(name: str) -> tuple[str, ...]:
    raw = _value(name)
    if not raw:
        return ()
    values = [item.strip() for item in raw.split(",")]
    if any(not item for item in values):
        raise ValueError(f"{name} contains an empty model entry")
    for item in values:
        provider, separator, model = item.partition("::")
        if (
            not separator or provider.casefold() not in _CLOUD_PROVIDER_NAMES | {"ollama"}
            or not model or len(model) > 200 or any(char.isspace() for char in model)
        ):
            raise ValueError(f"{name} entries must use provider::model-id format")
    return tuple(dict.fromkeys(values))


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
    openrouter_keys: tuple[str, ...]
    openrouter_base_url: str
    default_model: str
    fallback_models: tuple[str, ...]
    provider_order: tuple[str, ...]
    ai_request_timeout: float
    ai_max_retries_per_key: int
    ai_max_total_attempts: int
    ai_circuit_breaker_threshold: int
    ai_circuit_breaker_cooldown: float
    ai_free_only: bool
    ai_allow_paid_fallback: bool
    ai_max_estimated_cost_usd: float
    ai_free_model_allowlist: tuple[str, ...]
    ai_tool_capable_models: tuple[str, ...]
    ai_image_capable_models: tuple[str, ...]
    ai_streaming_models: tuple[str, ...]
    openai_key: str
    openai_keys: tuple[str, ...]
    openai_base_url: str
    openai_model: str
    anthropic_key: str
    anthropic_keys: tuple[str, ...]
    anthropic_base_url: str
    anthropic_model: str
    gemini_key: str
    gemini_keys: tuple[str, ...]
    gemini_base_url: str
    gemini_model: str
    groq_key: str
    groq_keys: tuple[str, ...]
    groq_base_url: str
    groq_model: str
    together_key: str
    together_keys: tuple[str, ...]
    together_base_url: str
    together_model: str
    sambanova_key: str
    sambanova_keys: tuple[str, ...]
    sambanova_base_url: str
    sambanova_model: str
    cloudflare_key: str
    cloudflare_keys: tuple[str, ...]
    cloudflare_account_id: str
    cloudflare_base_url: str
    cloudflare_model: str
    huggingface_key: str
    huggingface_keys: tuple[str, ...]
    huggingface_base_url: str
    huggingface_model: str
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
    vision_model: str
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
    user_monthly_request_limit: int
    user_monthly_cost_limit_usd: float
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
    malware_scan_enabled: bool
    clamav_socket_path: Path
    log_level: str
    local_bot_api_base_url: str
    local_bot_api_file_url: str
    dashboard_enabled: bool
    dashboard_host: str
    dashboard_port: int
    dashboard_auth_token: str
    mini_app_url: str
    mini_app_host: str
    mini_app_port: int
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
        openrouter_keys = _provider_keys("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY")
        openai_keys = _provider_keys("OPENAI_API_KEYS", "OPENAI_API_KEY")
        anthropic_keys = _provider_keys("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")
        gemini_keys = _provider_keys("GEMINI_API_KEYS", "GEMINI_API_KEY")
        groq_keys = _provider_keys("GROQ_API_KEYS", "GROQ_API_KEY")
        together_keys = _provider_keys("TOGETHER_API_KEYS", "TOGETHER_API_KEY")
        sambanova_keys = _provider_keys("SAMBANOVA_API_KEYS", "SAMBANOVA_API_KEY")
        cloudflare_keys = _provider_keys_with_aliases(
            "CLOUDFLARE_API_TOKENS", "CLOUDFLARE_API_KEY", ("CLOUDFLARE_API_KEYS",),
        )
        huggingface_keys = _provider_keys("HUGGINGFACE_API_KEYS", "HUGGINGFACE_API_KEY")
        api_key = openrouter_keys[0] if openrouter_keys else ""
        openai_key = openai_keys[0] if openai_keys else ""
        anthropic_key = anthropic_keys[0] if anthropic_keys else ""
        gemini_key = gemini_keys[0] if gemini_keys else ""
        groq_key = groq_keys[0] if groq_keys else ""
        together_key = together_keys[0] if together_keys else ""
        sambanova_key = sambanova_keys[0] if sambanova_keys else ""
        cloudflare_key = cloudflare_keys[0] if cloudflare_keys else ""
        huggingface_key = huggingface_keys[0] if huggingface_keys else ""
        cloudflare_account_id = _value("CLOUDFLARE_ACCOUNT_ID")
        ollama_url = _value("OLLAMA_BASE_URL").rstrip("/")
        for name, value in (
            ("OPENROUTER_API_KEY", api_key), ("OPENAI_API_KEY", openai_key),
            ("ANTHROPIC_API_KEY", anthropic_key), ("GEMINI_API_KEY", gemini_key),
            ("GROQ_API_KEY", groq_key), ("TOGETHER_API_KEY", together_key),
            ("SAMBANOVA_API_KEY", sambanova_key), ("CLOUDFLARE_API_KEY", cloudflare_key),
            ("HUGGINGFACE_API_KEY", huggingface_key),
        ):
            if value.startswith("replace-"):
                raise ValueError(f"{name} must contain a real key or be left blank")
        for name, keys in (
            ("OPENROUTER_API_KEYS", openrouter_keys),
            ("OPENAI_API_KEYS", openai_keys),
            ("ANTHROPIC_API_KEYS", anthropic_keys),
            ("GEMINI_API_KEYS", gemini_keys),
            ("GROQ_API_KEYS", groq_keys),
            ("TOGETHER_API_KEYS", together_keys),
            ("SAMBANOVA_API_KEYS", sambanova_keys),
            ("CLOUDFLARE_API_KEYS", cloudflare_keys),
            ("HUGGINGFACE_API_KEYS", huggingface_keys),
        ):
            if any(key.startswith("replace-") for key in keys):
                raise ValueError(f"{name} must contain real keys or be left blank")
        provider_order = _provider_order()
        default_model = _value("OPENROUTER_MODEL") or _value("DEFAULT_AI_MODEL", "auto")
        cloudflare_base_url = _value("CLOUDFLARE_BASE_URL").rstrip("/")
        if not cloudflare_base_url and cloudflare_account_id:
            cloudflare_base_url = (
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{cloudflare_account_id}/ai/v1"
            )
        if cloudflare_key and not cloudflare_account_id:
            raise ValueError(
                "CLOUDFLARE_ACCOUNT_ID is required when CLOUDFLARE_API_KEY is configured",
            )
        if cloudflare_key and not _value("CLOUDFLARE_MODEL"):
            raise ValueError(
                "CLOUDFLARE_MODEL is required when CLOUDFLARE_API_KEY is configured",
            )
        if cloudflare_account_id and not re.fullmatch(r"[A-Fa-f0-9]{32}", cloudflare_account_id):
            raise ValueError(
                "CLOUDFLARE_ACCOUNT_ID must be a 32-character hexadecimal ID",
            )
        huggingface_base_url = _value(
            "HUGGINGFACE_BASE_URL", "https://router.huggingface.co/v1",
        ).rstrip("/")
        provider_models = {
            "openrouter": bool(api_key),
            "gemini": bool(gemini_key and _value("GEMINI_MODEL")),
            "groq": bool(groq_key and _value("GROQ_MODEL")),
            "cloudflare": bool(
                cloudflare_key and cloudflare_account_id and cloudflare_base_url
                and _value("CLOUDFLARE_MODEL")
            ),
            "huggingface": bool(huggingface_key and _value("HUGGINGFACE_MODEL")),
            "ollama": bool(ollama_url and _value("OLLAMA_MODEL")),
        }
        if not any(provider_models.values()):
            raise ValueError("Configure at least one provider key/endpoint and its model ID")
        if gemini_key and not _value("GEMINI_MODEL"):
            raise ValueError("GEMINI_MODEL is required when GEMINI_API_KEY is configured")
        for key, model, provider in ((groq_key, "GROQ_MODEL", "GROQ_API_KEY"),):
            if key and not _value(model):
                raise ValueError(f"{model} is required when {provider} is configured")
        if huggingface_key and not _value("HUGGINGFACE_MODEL"):
            raise ValueError("HUGGINGFACE_MODEL is required when HUGGINGFACE_API_KEY is configured")
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
            openrouter_keys=openrouter_keys,
            openrouter_base_url=_value("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            default_model=default_model,
            fallback_models=_csv("FALLBACK_MODELS"),
            provider_order=provider_order,
            ai_request_timeout=_float("AI_REQUEST_TIMEOUT", 30, 1),
            ai_max_retries_per_key=_int("AI_MAX_RETRIES_PER_KEY", 1, 0, 3),
            ai_max_total_attempts=_int("AI_MAX_TOTAL_ATTEMPTS", 5, 1, 20),
            ai_circuit_breaker_threshold=_int("AI_CIRCUIT_BREAKER_THRESHOLD", 3, 1, 20),
            ai_circuit_breaker_cooldown=_float("AI_CIRCUIT_BREAKER_COOLDOWN", 60, 1),
            ai_free_only=_bool("AI_FREE_ONLY", False),
            ai_allow_paid_fallback=_bool("AI_ALLOW_PAID_FALLBACK", False),
            ai_max_estimated_cost_usd=_float("AI_MAX_ESTIMATED_COST_USD", 0, 0),
            ai_free_model_allowlist=_model_allowlist("AI_FREE_MODEL_ALLOWLIST"),
            ai_tool_capable_models=_model_allowlist("AI_TOOL_CAPABLE_MODELS"),
            ai_image_capable_models=_model_allowlist("AI_IMAGE_CAPABLE_MODELS"),
            ai_streaming_models=_model_allowlist("AI_STREAMING_MODELS"),
            openai_key=openai_key,
            openai_keys=openai_keys,
            openai_base_url=_value("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_model=_value("OPENAI_MODEL"),
            anthropic_key=anthropic_key,
            anthropic_keys=anthropic_keys,
            anthropic_base_url=_value("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
            anthropic_model=_value("ANTHROPIC_MODEL"),
            gemini_key=gemini_key,
            gemini_keys=gemini_keys,
            gemini_base_url=_value("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
            gemini_model=_value("GEMINI_MODEL"),
            groq_key=groq_key,
            groq_keys=groq_keys,
            groq_base_url=_value(
                "GROQ_BASE_URL", "https://api.groq.com/openai/v1",
            ).rstrip("/"),
            groq_model=_value("GROQ_MODEL"),
            together_key=together_key,
            together_keys=together_keys,
            together_base_url=_value(
                "TOGETHER_BASE_URL", "https://api.together.xyz/v1",
            ).rstrip("/"),
            together_model=_value("TOGETHER_MODEL"),
            sambanova_key=sambanova_key,
            sambanova_keys=sambanova_keys,
            sambanova_base_url=_value(
                "SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1",
            ).rstrip("/"),
            sambanova_model=_value("SAMBANOVA_MODEL"),
            cloudflare_key=cloudflare_key,
            cloudflare_keys=cloudflare_keys,
            cloudflare_account_id=cloudflare_account_id,
            cloudflare_base_url=cloudflare_base_url,
            cloudflare_model=_value("CLOUDFLARE_MODEL"),
            huggingface_key=huggingface_key,
            huggingface_keys=huggingface_keys,
            huggingface_base_url=huggingface_base_url,
            huggingface_model=_value("HUGGINGFACE_MODEL"),
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
            vision_model=_value("VISION_MODEL", "google/gemini-2.5-flash:free"),
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
            user_monthly_request_limit=_int("USER_MONTHLY_REQUEST_LIMIT", 1200, 1, 100_000),
            user_monthly_cost_limit_usd=_float("USER_MONTHLY_COST_LIMIT_USD", 50.0, 0),
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
            malware_scan_enabled=_bool("MALWARE_SCAN_ENABLED", False),
            clamav_socket_path=Path(_value("CLAMAV_SOCKET_PATH", "/var/run/clamav/clamd.ctl")),
            log_level=_value("LOG_LEVEL", "INFO").upper(),
            local_bot_api_base_url=_value("LOCAL_BOT_API_BASE_URL"),
            local_bot_api_file_url=_value("LOCAL_BOT_API_FILE_URL"),
            dashboard_enabled=_bool("DASHBOARD_ENABLED", True),
            dashboard_host=_value("DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=_int("DASHBOARD_PORT", 8787, 1024, 65535),
            dashboard_auth_token=_value("DASHBOARD_AUTH_TOKEN"),
            mini_app_url=_value("MINI_APP_URL").rstrip("/"),
            mini_app_host=_value("MINI_APP_HOST", "127.0.0.1"),
            mini_app_port=_int("MINI_APP_PORT", 8788, 1024, 65535),
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
        if settings.ai_request_timeout > 300:
            raise ValueError("AI_REQUEST_TIMEOUT must be between 1 and 300 seconds")
        if settings.ai_circuit_breaker_cooldown > 86_400:
            raise ValueError("AI_CIRCUIT_BREAKER_COOLDOWN must be at most 86400 seconds")
        if settings.ai_allow_paid_fallback and settings.ai_max_estimated_cost_usd <= 0:
            raise ValueError(
                "AI_MAX_ESTIMATED_COST_USD must be positive when AI_ALLOW_PAID_FALLBACK is enabled",
            )
        if settings.dashboard_host != "127.0.0.1":
            raise ValueError("DASHBOARD_HOST must be 127.0.0.1; access it through an SSH tunnel")
        if settings.mini_app_host != "127.0.0.1":
            raise ValueError("MINI_APP_HOST must be 127.0.0.1; publish it through an HTTPS reverse proxy")
        if settings.mini_app_url:
            mini_url = urlsplit(settings.mini_app_url)
            if (
                mini_url.scheme != "https" or not mini_url.hostname or mini_url.username
                or mini_url.password or mini_url.path not in {"", "/"}
                or mini_url.query or mini_url.fragment
            ):
                raise ValueError("MINI_APP_URL must be an HTTPS origin with no path, query, or fragment")
            try:
                public_port = mini_url.port
            except ValueError as exc:
                raise ValueError("MINI_APP_URL contains an invalid HTTPS port") from exc
            if public_port == 0:
                raise ValueError("MINI_APP_URL must use a valid HTTPS port")
            if settings.mini_app_port == settings.dashboard_port:
                raise ValueError("MINI_APP_PORT and DASHBOARD_PORT must be different")
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
            ("GROQ_API_KEY", settings.groq_key),
            ("TOGETHER_API_KEY", settings.together_key),
            ("SAMBANOVA_API_KEY", settings.sambanova_key),
            ("CLOUDFLARE_API_KEY", settings.cloudflare_key),
            ("HUGGINGFACE_API_KEY", settings.huggingface_key),
            ("HA_TOKEN", settings.ha_token),
            ("APP_DATA_KEY", settings.app_data_key),
            ("DASHBOARD_AUTH_TOKEN", settings.dashboard_auth_token),
            ("MINI_APP_URL", settings.mini_app_url),
        ):
            if any(char in value for char in "\r\n"):
                raise ValueError(f"{name} must be a single-line value")
        for name, keys in (
            ("OPENROUTER_API_KEYS", settings.openrouter_keys),
            ("OPENAI_API_KEYS", settings.openai_keys),
            ("ANTHROPIC_API_KEYS", settings.anthropic_keys),
            ("GEMINI_API_KEYS", settings.gemini_keys),
            ("GROQ_API_KEYS", settings.groq_keys),
            ("TOGETHER_API_KEYS", settings.together_keys),
            ("SAMBANOVA_API_KEYS", settings.sambanova_keys),
            ("CLOUDFLARE_API_KEYS", settings.cloudflare_keys),
            ("HUGGINGFACE_API_KEYS", settings.huggingface_keys),
        ):
            if any(any(char in key for char in "\r\n") for key in keys):
                raise ValueError(f"{name} must contain only single-line values")
        for name, value in (
            ("OPENROUTER_BASE_URL", settings.openrouter_base_url),
            ("OPENAI_BASE_URL", settings.openai_base_url),
            ("ANTHROPIC_BASE_URL", settings.anthropic_base_url),
            ("GEMINI_BASE_URL", settings.gemini_base_url),
            ("GROQ_BASE_URL", settings.groq_base_url),
            ("HUGGINGFACE_BASE_URL", settings.huggingface_base_url),
        ):
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError(f"{name} must be a valid HTTPS URL")
        if settings.cloudflare_base_url:
            parsed = urlsplit(settings.cloudflare_base_url)
            if (
                parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
            ):
                raise ValueError("CLOUDFLARE_BASE_URL must be a valid HTTPS URL")
        if settings.ollama_base_url:
            local_url = urlsplit(settings.ollama_base_url)
            if (not local_url.hostname or local_url.scheme not in {"http", "https"} or local_url.username
                    or local_url.password or local_url.query or local_url.fragment):
                raise ValueError("OLLAMA_BASE_URL must be a valid HTTP(S) URL")
            if local_url.scheme == "http" and local_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote OLLAMA_BASE_URL must use HTTPS")
        if settings.media_style not in {"balanced", "minimal", "expressive"}:
            raise ValueError("MEDIA_STYLE must be balanced, minimal, or expressive")
        if settings.malware_scan_enabled and (
            not settings.clamav_socket_path.is_absolute()
            or len(os.fsencode(settings.clamav_socket_path)) > 103
        ):
            raise ValueError(
                "CLAMAV_SOCKET_PATH must be an absolute path that fits the Unix socket path limit"
            )
        if settings.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        if not settings.local_bot_api_base_url and settings.max_upload_bytes > 20 * 1024 * 1024:
            raise ValueError("MAX_UPLOAD_BYTES above 20 MB requires a configured local Bot API server")
        return settings
