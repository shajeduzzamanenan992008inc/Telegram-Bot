# Changelog

## 2026-10-16

- Added encrypted persistent Mission Control records with owner-scoped status/report/pause/resume/cancel/delete commands, bounded multilingual analysis planning, usage-budget enforcement, progress messages, restart-to-paused recovery, and retention cleanup. Missions expose no arbitrary tools or shell execution.
- Added category-only feedback intake pause controls and a hash-bound evolution proposal lifecycle: encrypted patch/source retention, protected-path validation, rootless patch apply/test, admin review, one-shot staging approval, repeat sandbox verification, and a separate production-approval audit state.
- Added a `git apply`-capable network-disabled sandbox image, tests for mission persistence/privacy, patch validation/redaction, failed-test gates, hash mismatches, replay and admin authorization, and operator docs for both workflows.
- Production deployment, service restart, staging service health checks, and automatic rollback are not implemented; deployment approval is recorded without changing the live app.
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
