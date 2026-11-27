# Changelog

## 2026-11-27

- Fixed concurrent database use: all handlers, jobs and dashboards share one SQLite connection, so simultaneous explicit transactions failed with "cannot start a transaction within a transaction" (19 of 20 parallel `add_task` calls). Public `Database` coroutines are now serialized by a task-reentrant lock, and an interrupted, failed or cancelled operation rolls back anything it left pending instead of letting the next commit persist it.
- Fixed `.pptx` uploads: `presentation.slides[:500]` is not supported by python-pptx and made every deck fail with "Office document parse failed safely: AttributeError". Slides are now iterated safely and table cells, grouped shapes and speaker notes are extracted as well.
- Fixed file routing for RIFF containers: WAV audio and AVI video were claimed by the image adapter and silently ignored. Only WebP is treated as an image; WAV goes to transcription and AVI to the video adapter.
- Fixed conversation/document exports: PDF export dropped fenced code blocks and rendered the rest of the document as code; TXT export stripped `#` from code comments and shebangs. Code, indentation and truncated (unterminated) fences are now preserved.
- Fixed greetings and other tool-suggesting messages ("hello", "hi", "which", ...) failing with "AI service unavailable" when no configured model is declared tool-capable (the default for Groq, Gemini, Cloudflare and Hugging Face). The chat flow now answers normally without tools in that case.
- Fixed the maintenance loop: one failing housekeeping step no longer blocks the steps after it, and due reminders are delivered first and independently of backups and cleanup.
- Fixed embedded dashboard/Mini App servers replacing the bot's SIGINT/SIGTERM handlers on uvicorn >= 0.29 (the old `install_signal_handlers` hook is ignored there); both now use a shared builder that disables signal capture on every uvicorn version.
- Fixed `prepare_deploy_env.py`: it now validates exactly the providers the application can route to (OpenRouter, Gemini, Groq, Cloudflare, Hugging Face, Ollama), including each provider's required model/account variables, instead of rejecting Groq/Cloudflare/Hugging Face-only setups and accepting unusable OpenAI/Anthropic-only ones.
- Fixed the inconsistent default database file name (`assistant.sqlite3` vs `telegram_ai.sqlite3`) in `prepare_deploy_env.py` and the README, and the README backup commands that pointed at a non-existent `/opt/enan-ultra/scripts` path (releases live under `/opt/enan-ultra/current`).
- `safe_filename` now maps dot-only names (`.`, `..`) to the default name; the Private Mode notice no longer shows literal `**` markers.
- Added the missing `.env.example` (documents all 146 settings and the exact lines the installer and `init_env.py` rewrite), `.gitignore`, and a `tests/` suite (Python and Node) with regression tests for every fix above; CI references both and previously could not run.

## 2026-10-17

- Changed admin `/update` to accept a ZIP document, validate and safely overlay project files while preserving environment/database/persistent state, then clean up and replace the Python process. Update authorization now uses the single `ADMIN_ID` from `.env` (falling back to the lowest numeric legacy `ADMIN_USER_IDS` entry when unset).

## 2026-10-16

- Added an administrator-only Telegram ZIP upload workflow with expiring chat-bound sessions, strict archive/path/type/size checks, allowlisted isolated staging, SHA-256 diff manifests, syntax validation, optional rootless offline sandbox tests, one-shot hash-bound staging review callbacks, audit events, and documented security/recovery boundaries. Production deployment and restart remain intentionally unimplemented.
- Added a separate one-shot production approval and root-owned, fixed-path deployment broker with manifest/archive/base-hash verification, root-owned code snapshots, constrained systemd restart, DB/Telegram identity health checks, journal recovery, rollback, audit, and resource ceilings. Deployment remains disabled by default; no production deployment or recovery drill was performed, and the observed Azure VM layout remains incompatible pending operator review.
- Added encrypted persistent Mission Control records with owner-scoped status/report/pause/resume/cancel/delete commands, bounded multilingual analysis planning, usage-budget enforcement, progress messages, restart-to-paused recovery, and retention cleanup. Missions expose no arbitrary tools or shell execution.
- Added category-only feedback intake pause controls and a hash-bound evolution proposal lifecycle: encrypted patch/source retention, protected-path validation, rootless patch apply/test, admin review, one-shot staging approval, repeat sandbox verification, and a separate production-approval audit state.
- Added a `git apply`-capable network-disabled sandbox image, tests for mission persistence/privacy, patch validation/redaction, failed-test gates, hash mismatches, replay and admin authorization, and operator docs for both workflows.
- ZIP workflow deployment remains disabled by default. Staging approval alone never deploys; production deployment requires explicit operator enablement and verified host prerequisites.
- Replaced unconditional sticker collection in a separate SQLite file with an admin-only, private-chat collector that is disabled by default and uses the existing async database.
- Added additive sticker catalog migrations, file-unique-ID deduplication, pending approval, pack metadata, tags, animation/dimension metadata, searchable categories, usage tracking and persistent per-user cooldowns.
- Added Bot API sticker-set imports with batch yielding, duplicate/failure counts, per-admin cancellation and same-pack concurrency protection; added admin review, categorization, search, stats and private metadata export commands.
- Restricted inline and automatic sticker retrieval to approved catalog rows; mark repeated Telegram send failures and fall back to another approved item while retaining delivered text.
- Migrated any existing `stickers_store.db` metadata into the main catalog pending review, and documented collection, import, categorization, database and troubleshooting workflows.
- Expanded deterministic theme vocabularies for common Roman Bangla topic phrases and made a confident, specific subject phrase eligible for an immediate theme switch; added programming → chemistry → astronomy transition coverage.
- Added theme/topic customization, Mini App setup, accessibility, security, multimodal, voice, document, KB, research, provider-capability, and privacy/retention guides documenting verified behavior and limitations.
- Added configurable provider order, strict free-model allowlists, estimated-cost gates, explicit per-model capability settings, and bounded provider request budgets.
- Changed credential failover to rotate keys only for confirmed invalid credentials; account quota/rate failures honor retry metadata, pause the provider, and continue with eligible cross-provider candidates.
- Added transient circuit breaking, persistent hashed credential quarantine, safe admin `/providerdiag` and explicit provider recheck, and sanitized provider failure alerts.
- Added per-user task prerequisites, blocked-task enforcement, dependency-aware task exports, and task creation by replying to a Telegram text/caption message.
- Added authenticated Mini App task APIs and responsive task-list controls for per-user add, completion, deletion, priority, deadlines, and prerequisite status.
- Added `/tasks plan <goal|chat>` with bounded structured generation, private-history planning, privacy-aware provider routing, usage-budget accounting, and atomic creation of prerequisite-linked personal tasks.
- Added optional ClamAV INSTREAM scanning before upload processing, fail-closed scanner errors, rate-limited malware alerts, and Ubuntu installation/service integration.
- Added provider setup, routing architecture, failover/retry, and free-tier/cost-control guides with corrected provider behavior documentation.

## 2026-10-15

- Added a shared Telegram response formatter and Bot API 10.1 Rich Message transport with ordinary-message compatibility fallback.
- Added private-chat native Thinking drafts, throttled token previews, Telegram stop-generation updates, `/cancel`, and partial-response persistence.
- Routed support notices and admin text replies through the shared renderer, with sender authorization, delivery feedback and audit records.
- Added `/providers`, admin health latency reporting, `/admin`, and an inline `/start` menu.
- Added private `/export md|txt|pdf` and Bengali shaping through ReportLab plus the host's Noto/Nirmala font.
- Added feature, architecture, Rich Message, admin, configuration, deployment and troubleshooting guides.
