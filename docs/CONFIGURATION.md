# Configuration

`.env.example` is the canonical, safe template and contains the complete set of supported environment variable names and defaults. Run `python scripts/init_env.py` to create a local `.env`; never copy real secrets into `.env.example` or commit `.env`.

## Required values

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather token; secret |
| `ADMIN_CHAT_ID` | Private support/admin chat |
| `ADMIN_ID` | Single user ID allowed to apply `/update` ZIPs; falls back to the lowest `ADMIN_USER_IDS` value if unset |
| `ADMIN_USER_IDS` | Comma-separated authorized numeric Telegram user IDs |
| `ACCESS_MODE`, `ALLOWED_USER_IDS` | Public/allowlist access policy |
| `APP_DATA_KEY` | Encryption key for Personal KB and encrypted database backups |
| `DASHBOARD_AUTH_TOKEN` | Dashboard authentication secret |

At least one provider must be configured. Prefer plural key variables for key rotation; the singular key is used when the plural variable is empty.

## Variable groups

The following exact names are read from `.env.example`/`Settings.load()`:

- **Brand/access:** `BOT_NAME`, `BOT_DISPLAY_NAME`, `AI_PERSONA_PROFILE`, `TELEGRAM_BOT_TOKEN`, `ADMIN_CHAT_ID`, `ADMIN_ID`, `ADMIN_USER_IDS`, `ALLOWED_USER_IDS`, `ACCESS_MODE`.
- **Providers/models:** `OPENROUTER_API_KEY`, `OPENROUTER_API_KEYS`, `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `DEFAULT_AI_MODEL`, `FALLBACK_MODELS`, `AI_PROVIDER_ORDER`, `GEMINI_API_KEY`, `GEMINI_API_KEYS`, `GEMINI_BASE_URL`, `GEMINI_MODEL`, `GROQ_API_KEY`, `GROQ_API_KEYS`, `GROQ_BASE_URL`, `GROQ_MODEL`, `CLOUDFLARE_API_KEY`, `CLOUDFLARE_API_KEYS`, `CLOUDFLARE_API_TOKENS`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_BASE_URL`, `CLOUDFLARE_MODEL`, `HUGGINGFACE_API_KEY`, `HUGGINGFACE_API_KEYS`, `HUGGINGFACE_BASE_URL`, `HUGGINGFACE_MODEL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `PROVIDER_REQUEST_RESERVE_USD`.
- **Provider reliability and routing:** `AI_REQUEST_TIMEOUT`, `AI_MAX_RETRIES_PER_KEY`, `AI_MAX_TOTAL_ATTEMPTS`, `AI_CIRCUIT_BREAKER_THRESHOLD`, `AI_CIRCUIT_BREAKER_COOLDOWN`, `AI_FREE_ONLY`, `AI_ALLOW_PAID_FALLBACK`, `AI_MAX_ESTIMATED_COST_USD`, `AI_FREE_MODEL_ALLOWLIST`, `AI_TOOL_CAPABLE_MODELS`, `AI_IMAGE_CAPABLE_MODELS`, `AI_STREAMING_MODELS`.
- **Agents/media:** `AGENTS_ENABLED_BY_DEFAULT`, `AGENT_MAX_STEPS`, `AGENT_MAX_WORKERS`, `AGENT_MAX_COST_USD`, `STT_MODEL`, `TTS_MODEL`, `TTS_VOICE`, `VISION_MODEL`, `IMAGE_MODEL`, `TRIGGER_WORDS`.
- **Storage and limits:** `DATABASE_PATH`, `HISTORY_MESSAGES`, `MAX_INPUT_CHARS`, `MAX_OUTPUT_CHARS`, `MAX_UPLOAD_BYTES`, `MAX_IMAGE_BYTES`, `MAX_IMAGE_PIXELS`, `MAX_ARCHIVE_EXPANDED_BYTES`, `MAX_ARCHIVE_FILES`, `MAX_ARCHIVE_DEPTH`, `MAX_DOCUMENT_PAGES`, `MAX_VIDEO_SECONDS`, `MAX_VIDEO_FRAMES`, `MAX_AUDIO_SECONDS`, `REQUEST_COOLDOWN_SECONDS`, `USER_DAILY_REQUEST_LIMIT`, `USER_MONTHLY_REQUEST_LIMIT`, `USER_MONTHLY_COST_LIMIT_USD`, `MAX_DAILY_COST_USD`, `IMAGE_COST_RESERVE_USD`, `TTS_COST_RESERVE_USD`, `WEB_SEARCH_COST_RESERVE_USD`, `DOCUMENT_RETENTION_HOURS`, `MAX_PENDING_REMINDERS`, `MAX_DOCUMENT_CHUNKS`.
- **Telegram ZIP updates:** `UPDATE_UPLOAD_WINDOW_SECONDS` (default 300; range 30–900), `UPDATE_MAX_FILES`, `UPDATE_MAX_DEPTH`, `UPDATE_MAX_PATH_LENGTH`, `UPDATE_MAX_COMPRESSION_RATIO`, `UPDATE_EXTRACT_TIMEOUT_SECONDS`, `UPDATE_TEST_TIMEOUT_SECONDS`, `UPDATE_DEPLOYMENT_ENABLED`, and `UPDATE_DEPLOYMENT_SOCKET_PATH`. Upload and expanded sizes reuse `MAX_UPLOAD_BYTES` and `MAX_ARCHIVE_EXPANDED_BYTES`. ZIP uploads are disabled by default; `/update enable` opens one temporary upload slot for configured admins. The bot never overlays the running checkout. A separate digest-bound production approval submits to the optional broker, disabled by default. The broker uses root-only interactive approval unless its root-controlled process environment explicitly sets `TELEGRAM_AI_AUTO_DEPLOY_ADMIN_IDS`; an empty or unset list preserves manual approval. This broker variable is not a bot setting and should not be placed only in the bot `.env`. See [`TELEGRAM_ZIP_UPDATES.md`](./TELEGRAM_ZIP_UPDATES.md) and [`UPDATE_SECURITY.md`](./UPDATE_SECURITY.md).
- **Memory and features:** `MEMORY_ENABLED_BY_DEFAULT`, `PERSONAL_KB_ENABLED`, `APP_DATA_KEY`, `PERSONAL_KB_MAX_DOCUMENTS`, `PERSONAL_KB_MAX_BYTES`, `REMOTE_FILE_FALLBACK`, `WEB_SEARCH_ENABLED`, `IMAGE_GENERATION_ENABLED`, `VOICE_INPUT_ENABLED`, `VOICE_OUTPUT_MODE`, `STICKER_ENABLED`, `STICKER_FILE_IDS`, `MEDIA_STYLE`, `ADMIN_ALERTS`, `MALWARE_SCAN_ENABLED`, `CLAMAV_SOCKET_PATH`, `LOG_LEVEL`.

`STICKER_ENABLED` and `STICKER_FILE_IDS` control allowlisted model-requested sticker actions. The metadata catalog collector is a separate admin-controlled database setting, starts off, and is toggled with `/collect_on` and `/collect_off`; see [sticker collection](./STICKER_COLLECTION.md).
- **Transport/dashboard/integrations:** `LOCAL_BOT_API_BASE_URL`, `LOCAL_BOT_API_FILE_URL`, `DASHBOARD_ENABLED`, `DASHBOARD_HOST`, `DASHBOARD_PORT`, `DASHBOARD_AUTH_TOKEN`, `HA_BASE_URL`, `HA_TOKEN`, `HA_ALLOWED_ENTITIES`, `HA_ALLOWED_SERVICES`.
- **Telegram Mini App:** `MINI_APP_URL`, `MINI_APP_HOST`, `MINI_APP_PORT`.
- **Code sandbox:** `CODE_SANDBOX_ENABLED`, `CODE_SANDBOX_IMAGE`, `CODE_SANDBOX_TIMEOUT_SECONDS`, `CODE_SANDBOX_MEMORY_MB`, `CODE_SANDBOX_CPU_COUNT`, `CODE_SANDBOX_MAX_FILES`, `CODE_SANDBOX_MAX_EXPANDED_BYTES`.

Mission Control reuses the existing agent limits: step ceiling, global workers, and estimated per-mission spend. It also honors the regular user and bot-wide usage budgets, provider attempt ceiling, and local-only/free-only routing. The mission active-per-user cap (3), mission-content retention (30 days), proposal count cap (30 total/5 per creator), proposal expiry (7 days), and proposal metadata retention are bounded in code.

## Important behavior

- OpenRouter, Groq, Gemini, Cloudflare and Hugging Face accept up to 10 comma-separated keys. The non-empty plural setting takes precedence over the singular key. Cloudflare key precedence is `CLOUDFLARE_API_TOKENS`, `CLOUDFLARE_API_KEYS`, `CLOUDFLARE_API_KEY`; Cloudflare also needs an account ID and model. `AI_PROVIDER_ORDER` may contain each supported cloud provider once. Local-only mode requires an Ollama URL and model; it does not fall back to cloud.
- A 401/confirmed invalid credential quarantines that specific key; the SHA-256 fingerprint state is saved beside `DATABASE_PATH`. A 402 or 429 is treated as account/provider scoped and cools down the provider without trying sibling keys. Transient network/5xx retries, request attempt limits and circuit state are described in [`FAILOVER_AND_RETRY.md`](FAILOVER_AND_RETRY.md). Admins can inspect local status with `/providerdiag` and explicitly reset/probe one provider with `/providerdiag recheck <provider>`.
- `AI_FREE_ONLY=true` requires exact `provider::model-id` entries in `AI_FREE_MODEL_ALLOWLIST`. Paid fallback remains disabled unless explicitly enabled; then `AI_MAX_ESTIMATED_COST_USD` must be positive. The cap uses estimates and is not a provider billing limit. See [`FREE_TIER_AND_COST_CONTROL.md`](FREE_TIER_AND_COST_CONTROL.md).
- `VISION_MODEL`, `IMAGE_MODEL`, `STT_MODEL` and `TTS_MODEL` must identify a model that supports the relevant capability on its provider.
- `LOCAL_BOT_API_BASE_URL` and `LOCAL_BOT_API_FILE_URL` must be set together. Rich Message endpoints need a compatible Bot API server; older servers fall back.
- `VOICE_OUTPUT_MODE` can disable voice or request output on voice input. A gTTS fallback sends text to Google's external service and is disabled in local-only mode.
- `APP_DATA_KEY` must remain stable to restore encrypted backups. `DASHBOARD_AUTH_TOKEN` is separate.
- The Mini App is disabled when `MINI_APP_URL` is blank. To enable it, set an HTTPS origin (for example `https://omega.example.com`) with no path, keep `MINI_APP_HOST=127.0.0.1`, and publish `MINI_APP_PORT` only through an HTTPS reverse proxy. The proxy must preserve the public `Host` and set `X-Forwarded-Proto: https`; the app verifies both before serving requests. Its port must differ from the admin dashboard port.
- `MAX_IMAGE_PIXELS`, archive limits, upload limits, output limits and usage budgets constrain untrusted input and resource usage.
- `ACCESS_MODE=public` intentionally allows ordinary users to use the bot. `ACCESS_MODE=allowlist` requires a non-empty `ALLOWED_USER_IDS` and denies users outside that list; configured admins remain authorized. Add `ADMIN_ID` to `ADMIN_USER_IDS` when that account also needs the other admin-only features.
- `/update` accepts ZIP uploads only from `ADMIN_ID` in `ADMIN_CHAT_ID`, scans and stages them, and requires the rootless sandbox tests to pass before review. The archive is a patch: omitted files are retained. Staging approval does not deploy. A separate production approval queues the exact candidate digest through the broker; activation also requires a root operator to verify the Telegram approval in an interactive terminal using `telegram-ai-deploy --authorize <UPDATE_ID>`. The bot's caller-supplied admin ID is only a claim. The broker creates a verified code snapshot, builds an immutable versioned source release, atomically switches `current`, restarts the fixed service, checks health, and attempts automatic source rollback on failure. It does not back up the database, install dependencies, or run database migrations. The broker and socket remain disabled until the target and staging gates pass.
- `HISTORY_MESSAGES` bounds the number of stored conversation turns supplied to the model. Their combined text is additionally bounded by `MAX_INPUT_CHARS`, retaining the newest history first; this character limit is not an exact tokenizer-based token limit.
- The system instruction requests Bengali, English, Roman Bangla and mixed-language understanding, including common informal spelling and contextual follow-ups. No language-specific model is silently substituted: Telegram chat uses the configured current model and provider fallback policy, while image input uses `VISION_MODEL`. Actual multilingual performance depends on the chosen model. Provider errors and empty model responses are reported as temporary service failures, not language-understanding failures.
- `MALWARE_SCAN_ENABLED=true` requires a running ClamAV daemon with the configured Unix socket (`CLAMAV_SOCKET_PATH`, default `/var/run/clamav/clamd.ctl`). Telegram uploads are scanned before local parsing or provider upload. Infected uploads are blocked and admin alerts are rate-limited; scanner errors fail closed. The generic development default is off, while the Ubuntu installer enables it for new deployments and enables it on existing configs only if the key was absent. ClamAV signature updates require the `clamav-freshclam` service.
- PDF export resolves Noto Sans Bengali on Ubuntu (installed by the installer) or Nirmala UI on Windows. There is no PDF font-path environment variable.

For descriptions/default values of every setting, consult the matching line in `.env.example` and validation in `src/enan_ultra/config.py`.
