# Enan Ultra — Feature Verification Audit

## Current feature matrix and Telegram menu review (2026-10-23)

This section records the current Windows working-tree review on branch `codex/admin-zip-update`, HEAD `4050a487d6b32ff4b6fc4ed92bd3f0de06abfc49`. The tree is intentionally dirty and includes existing repair work plus the command-menu changes. Results below distinguish source review and local mocked tests from real-service verification.

**Evidence labels:** `PASS` means the named local check ran and passed; `PARTIAL` means code and some local tests exist but an integration or operational condition is still unverified; `NOT TESTED` means no real integration was run; `BLOCKED` means target/staging facts are unavailable; `NOT IMPLEMENTED` means the feature is not present as a product capability. The full local suite is evidence for its test cases only, not a claim that every feature works in production.

### Telegram command classification

**Public commands (65):** `/start`, `/help`, `/reset`, `/clear`, `/live`, `/exit`, `/id`, `/voice`, `/make_sticker`, `/summary`, `/setmode`, `/read`, `/media`, `/style`, `/language`, `/format`, `/temporary`, `/persona`, `/timezone`, `/memory`, `/remember`, `/forget`, `/files`, `/forget_files`, `/privacy`, `/remind`, `/reminders`, `/cancelreminder`, `/snooze`, `/tasks`, `/search`, `/research`, `/historysearch`, `/document`, `/translate`, `/web`, `/image`, `/imagine`, `/usage`, `/cancel`, `/export`, `/theme`, `/app`, `/agents`, `/study`, `/kb`, `/feedback`, `/knowledge`, `/approve`, `/deny`, `/test`, `/patch`, `/emoji`, `/stickers`, `/preferences`, `/resetpreferences`, `/mission`, `/mission_new`, `/mission_status`, `/mission_list`, `/mission_pause`, `/mission_resume`, `/mission_cancel`, `/mission_report`, `/mission_delete`.

**Admin-only commands (34):** `/clearuser`, `/model`, `/models`, `/status`, `/providers`, `/providerdiag`, `/admin`, `/update`, `/end`, `/endall`, `/device`, `/vm`, `/block`, `/unblock`, `/collect_on`, `/collect_off`, `/import_sticker_set`, `/cancel_sticker_import`, `/sticker_stats`, `/sticker_search`, `/sticker_review`, `/sticker_categorize`, `/sticker_export`, `/evolve_status`, `/evolve_pending`, `/evolve_review`, `/evolve_test`, `/evolve_approve`, `/evolve_stage`, `/evolve_deploy_approve`, `/evolve_cancel`, `/learning_status`, `/learning_pause`, `/learning_resume`.

**Hidden compatibility aliases (2):** `/resetuser` aliases `/clearuser`; `/end_all` aliases `/endall`. Both remain registered and callable; neither command was renamed or removed. They are omitted from menus because the 65 public plus 34 admin commands already use 99 of Telegram's 100-command per-scope limit. Callback actions such as `zipupd:review`, `zipupd:approve`, `zipupd:reject`, `zipupd:cancel`, and `zipupd:deploy` are internal callback data, not slash commands, and are in no menu. `/approve`, `/deny`, `/test`, and `/patch` are public entry points whose specific requests remain subject to requester/approver checks.

Menu registration lives in `src/enan_ultra/command_menu.py`, is called by `BotHandlers.register_commands` during startup, and is reused whenever that catalog is registered. `BotCommandScopeDefault` contains public commands only; a negative `ADMIN_CHAT_ID` is explicitly set to the public list, configured admins receive per-user scopes (`BotCommandScopeChatMember` in the admin group and private `BotCommandScopeChat`), and `/update` is included only for the primary admin in its configured chat context. A private runtime-state file beside the configured database remembers the last admin IDs/chat so future roster or chat changes can delete obsolete scopes. The first rollout cannot enumerate old Telegram scopes for admins removed before this state file existed; that one-time legacy cleanup requires checking known former IDs with the live Bot API. Each failed Telegram API scope call logs only its exception class. Handler authorization remains independent of menu visibility: admin handlers gate the Telegram user ID; `/update` and its callbacks also enforce the exact configured chat and primary `ADMIN_ID`; the bot-supplied ID claim cannot authorize broker activation.

The menu-scope and denial tests use mocks. No live Telegram Bot API call or separate authorized/unauthorized account check was made, so the current menu shown by Telegram clients is **NOT TESTED**.

### Current findings by severity

| Severity | Finding | Result |
|---|---|---|
| `HIGH` | Intended Azure service, source provenance, ownership/config/state paths, Python runtime, and rollback remain unknown in current evidence. | Production compatibility and recovery remain blocked; do not enable the broker/socket. |
| `MEDIUM` | Telegram may retain broad or per-user command scopes from before this code began tracking the admin roster. Removed user IDs cannot be recovered from the local current configuration. | Check/delete legacy scopes for known former admins with a test bot or authorized read-only Bot API session; handler authorization still blocks operations. |
| `MEDIUM` | Current HEAD is unsigned, the worktree is dirty, GitHub CLI is unavailable, and no external CI run is tied to the modified tree. | The temporary wheel is integrity-checked but is not a reviewed release or provenance proof. |
| `LOW` | Telegram command-scope API failures are logged safely but the live menu may remain at its previous scope until a successful registration. | Confirm registration success with separate staging admin and regular accounts; do not infer it from mocks. |

No critical code defect was confirmed by the local checks; absence of a confirmed defect is not evidence that untested integrations are safe.

### Feature matrix

| Feature | Status | Code and local evidence | Limit of evidence / next verification |
|---|---|---|---|
| Command classification, default/admin scopes, error-safe registration | `PASS — MOCKED` | `command_menu.py`, `handlers.py`, `runtime.py`; `tests/test_command_menu.py` checks the full registered-command partition, default/group public menus, general-admin and primary-only `/update` scopes, removed-admin/chat scope cleanup, and safe API-error logging. | Telegram has not been contacted; confirm with separate test-bot admin and regular accounts. |
| Server-side admin commands and callbacks | `PARTIAL` | `_gate`, `/update`, `update_callback`, request ownership, and broker authorization in `handlers.py`, `telegram_zip_updates.py`, `telegram_deployment.py`; static source inventory found visible admin gates on all 34 admin command routes, with local denial, stale/replay and forged-claim tests. | Mocked Telegram identities/callbacks do not establish account/client behavior; run isolated test-bot cases. |
| ZIP intake, quarantine, validation, digest review, replay and restart handling | `PARTIAL` | `services/telegram_zip_updates.py`, `handlers.py`; `tests/test_telegram_zip_updates.py` covers invalid/path-hostile ZIPs, unauthorized user/chat, scanner unavailable, test failure, changed/stale digest, replay, one-shot decisions, and manager reinitialization. | Local temp-directory simulation only; no real Telegram upload, daemon scan or systemd process restart. |
| ClamAV | `PARTIAL` | `services/antivirus.py`; mocked scanner success, malware, malformed result and unavailable-scanner tests. The ZIP path fails closed on scanner errors. | No ClamAV daemon/socket/signature check was run. |
| Candidate syntax and sandbox | `PARTIAL` | `services/code_sandbox.py`, `services/telegram_zip_updates.py`; tests cover validation, blocked test outcomes, time limits, isolated candidate state and failure cleanup. | Rootless container isolation has not been exercised on Linux; mocks are not a sandbox escape test. |
| Broker authorization, snapshots, atomic switch, health and rollback | `PARTIAL` locally; production `BLOCKED` | `deploy/telegram_ai_deploy.py`; `tests/test_telegram_deployment.py` covers peer/claim restrictions, digest binding, replay/concurrency, backup failure, activation interruption, restart/health failure and simulated rollback. | Fake peer credentials/systemd and temp paths only; no real socket, systemd, service user, backup restore or rollback drill. |
| AI providers, routing, fallback, quota/circuit handling | `PARTIAL` | `services/providers.py`, `openrouter.py`, `key_rotation.py`; `tests/test_provider_reliability.py` uses mock transports and responses. | No live provider call, current credential validation, latency, quota or paid-fallback check. |
| Voice, image, media and document processing | `PARTIAL` | `services/voice.py`, `media.py`, file/media handlers; local parser and handler tests. | No live STT/TTS, image-generation or model call; provider and Telegram media transport are unverified. |
| Coding, project tests, patch and evolution workflow | `PARTIAL` | `test_project`, `patch_project`, evolution handlers and `services/code_sandbox.py`; local policy/hash/sandbox-result tests. | No real Linux rootless sandbox or end-to-end Telegram review. |
| Study, personal knowledge base, missions and agents | `PARTIAL` | `study`/mission handlers, `services/mission_control.py`, `services/agents.py`, SQLite storage; local ownership, scope, persistence and concurrency tests. | Temporary databases and mocked model work only; no production state or provider run. |
| Web search/read and URL/image security | `PARTIAL` | `live_search`, `read_page`, URL validation and external-image code; local mock HTTP/DNS and policy tests. | No live search/API or external website fetch was made. |
| Home Assistant and VM automation | `PARTIAL` / environment `BLOCKED` | `actions.py`, HA/VM clients, admin gates and allowlisted-action paths. | No target HA/VM configuration, endpoint, service identity or real action was checked or invoked. |
| Admin alerts, deployment-result notification and failure handling | `PARTIAL` | `runtime.py`, `services/telegram_messages.py`; local mocked send/fallback and persisted-result paths. | No live Telegram delivery, outage, retry or post-restart alert test. |
| SQLite memory, profiles, reminders, approvals and migrations | `PARTIAL` | `storage/database.py`; temporary SQLite tests, including online-backup/restore helper behavior. | No production DB read, migration, backup or restore. |
| Mini App, themes and sticker features | `PARTIAL` | `mini_app.py`, `webapp/`, `theme_engine.py`, `sticker_collector.py`; ASGI/database/JS tests. | Public HTTPS launch, Telegram WebApp validation and live sticker permissions are not verified. |
| Supported Python and CI coverage | `PASS — CONFIG REVIEW`; current remote run `NOT TESTED` | `pyproject.toml` declares `>=3.11,<3.16`; `.github/workflows/tests.yml` declares Python 3.11–3.15 test jobs plus a 3.15 quality/build job. | No CI run is bound to this dirty worktree; workflow configuration is not a CI result. |
| Current release artifact/provenance | `BLOCKED` | A fresh wheel from the final dirty tree was built in a unique temporary directory; ZIP integrity and 13 expected web assets passed (SHA-256 `ab0711429880ac1a21178b261d73ddcbeab8325ba6975a7dce57f9a36b45b8f6`). Historic artifacts point to other source states. | The temporary wheel is not a reviewed release artifact and has no current CI attestation or signature. Do not use older artifacts as proof; build/sign/attest from a reviewed commit. |
| Autonomous model training | `NOT IMPLEMENTED` | Learning features aggregate opt-in feedback/preferences; they do not train or fine-tune a model. | Keep product wording explicit; provider fine-tuning is a separate unsupported workflow. |
| Real Azure target, Ubuntu/systemd, target compatibility and recovery | `BLOCKED` | Current workspace broker/unit target `telegram-ai.service`, `enan-ultra`, `/opt/enan-ultra`; historical Azure observations are not current evidence. | No current Azure read-only inventory or disposable Ubuntu/systemd staging evidence is available. Keep production `NO-GO`. |

### Checks actually run for this snapshot

- `uv run --isolated --locked --python 3.15 --group test pytest -q` — **247 passed** on Windows/Python 3.15.
- Menu, ZIP and update-idempotency subset after the final duplicate-registration assertion — **51 passed**. The prior full suite had **247 passed**; production code did not change after that full run.
- `uv lock --check` — **passed**, 55 locked packages resolved.
- `pip-audit 2.10.1` on the locked build/test export — **No known vulnerabilities found**; this is a local audit, not CI evidence.
- Ruff (`E4,E7,E9,F`), Python `compileall`, frontend `node --check`, and `node --test tests/test_webapp_theme_ui.cjs` — **passed** (2 JavaScript tests).
- `git diff --check` — **passed**; Git reports existing CRLF-to-LF normalization notices on changed files.
- Telegram, providers, ClamAV, Linux/systemd, rootless runtime, Azure, and production data checks — **not run**.

> **Historical audit (2026-10-17).** Its Azure observations and test counts are not current host evidence. For the current workspace and ZIP workflow, see [deployment readiness](./DEPLOYMENT_READINESS_REVIEW.md). The statement below about direct ZIP extraction describes a superseded intermediate state; the current handler stages and reviews ZIPs and does not overwrite the live tree.

## Audit scope and safety

- Reviewed the local repository's implementation, feature catalogue, tests, CI workflow, and Ubuntu deployment documentation.
- Ran the local test suite and a read-only status query against the Azure VM using the SSH command supplied by the owner.
- Did not send Telegram messages, call AI providers, run database migrations, deploy code, edit production files, or restart services.
- This report is the only file created for this audit. Other modified/untracked workspace files were already present before this audit and were left untouched.
- `PASS` means the relevant local test or check ran successfully; it does not imply a live integration is healthy.

## Overall verdict

**At the time of this original audit, 154 collected local tests passed, but live Telegram/provider behavior was not verified and the ZIP flow had no production deploy/health/rollback implementation.** Later local changes replaced the direct-extraction intermediate state with staged review and a separately approved, versioned-release broker. These changes have local mocked coverage but have not been installed or verified on Azure. See the current reassessment linked above.

The Azure service query returned `ActiveState=active` and `SubState=running`, but this is not sufficient evidence of application health. The VM runs a different checkout/layout from this workspace: systemd starts `/home/azureenan/personal-telegram-ai/assistant.py` as user `azureenan`; this repository's installer and unit expect `/opt/enan-ultra` and `python -m enan_ultra` as user `enan-ultra`. The VM also reported `NRestarts=13968`. No error- or warning-priority journal records were returned for the preceding hour, but that does not establish that Telegram polling, providers, ClamAV, or database operations work.

**Credential incident:** a read-only attempt to list remote `.env` variable names was incorrectly quoted and printed secret values into command output. No secret values are reproduced in this report. Treat the exposed OpenRouter API key as compromised and revoke/replace it immediately. Treat the Telegram API hash shown in that output as disclosed as well; replace the associated Telegram application credentials if possible. Do not reuse the exposed values.

## Feature verification

| Feature | Status | Evidence | Test performed | Result | Remaining work |
|---|---|---|---|---|---|
| Telegram commands, message routing, and authorization | PARTIAL | Command registration and message routing are in `src/enan_ultra/handlers.py` and `src/enan_ultra/runtime.py`; the handlers use the configured admin/user gates. | Full local suite; handler tests use constructed Telegram updates and mocks. | Local tests passed; no live Telegram command was sent. | Exercise commands with a dedicated test bot and authorized/non-authorized accounts. |
| Bengali, English, Roman Bangla, and follow-up context | PARTIAL | Prompt/context path in `handlers.py`; focused cases in `tests/test_nlu_context.py`. | Local NLU/context tests. | Tests passed. Language quality is generated by the configured model, not proven by deterministic tests. | Verify representative language cases against the selected provider/model. |
| Conversation history, profile, and explicit/opt-in memory | PARTIAL | `src/enan_ultra/storage/database.py`, `handlers.py`; tests in `test_nlu_context.py`, `test_user_profile_web_search.py`, and `test_final_upgrade.py`. | Local persistence, isolation, and context tests. | Tests passed against temporary/test databases. | Confirm deployed database path, permissions, backup, and restore without touching production data. |
| AI providers, routing, quota handling, and failover | PARTIAL | Adapters and routing in `services/providers.py`, `services/openrouter.py`, and `services/provider_errors.py`; `tests/test_provider_reliability.py`. | Mock HTTP transports and simulated provider responses. | Mocked retry/failover tests passed; no live provider call was made. | Rotate the exposed key, then verify each configured provider with a safe account/test environment. |
| Streaming, typing, and rich Telegram messages | PARTIAL | `services/telegram_messages.py`, `handlers.py`; `tests/test_media_routes.py` and `tests/test_telegram_messages.py`. | Local handler/client mocks. | Tests passed; Telegram Bot API/client-specific live behavior not verified. | Verify supported Bot API behavior with a test bot. |
| Themes and Telegram Mini App | PARTIAL | `mini_app.py`, `webapp/`, `services/theme_engine.py`; `tests/test_mini_app.py` and `test_adaptive_theme.py`. | ASGI test client, signed-init-data checks, user-isolation and streaming tests. | Local tests passed. | Validate the real HTTPS/reverse-proxy/Tunnel and Telegram Web App launch configuration. |
| Stickers and sticker collection/import | PARTIAL | `sticker_collector.py`, database migrations and handler registrations; `tests/test_sticker_collector.py`. | Local database tests and mocked Telegram/API callbacks. | Tests passed. | Verify the live bot's Telegram sticker permissions and admin review workflow. |
| Voice, image, document, and media processing | PARTIAL | `services/media.py`, `services/voice.py`, file registry, and media handlers; `test_media_routes.py`, `test_vision_handler.py`, and `test_final_upgrade.py`. | Local parser/handler tests and mocked provider transports. | Tests passed; external transcription, TTS, image generation, and model calls were not performed. | Test only with a test bot and non-sensitive sample files after provider credentials are rotated. |
| Security boundaries and admin approvals | PARTIAL | `security.py`, handler gates, database audit/approval paths; security/approval cases across `test_final_upgrade.py`, `test_omega_mission_evolution.py`, `test_mini_app.py`, and `test_telegram_zip_updates.py`. | Local authorization, signature, replay, and path-policy tests. | Tests passed. These tests do not prove the remote VM's permissions or configuration. | Compare production permissions and environment configuration against the intended deployment after the checkout mismatch is resolved. |
| Antivirus scanning | PARTIAL | `services/antivirus.py`; `tests/test_antivirus.py`; Ubuntu install documentation configures ClamAV. | Mocked ClamAV socket responses, malware and fail-closed cases. | Local tests passed; no live daemon/socket/signature check was run on the VM. | Verify the actual service's ClamAV socket access and signature freshness in an approved maintenance window. |
| Self-learning and feedback | PARTIAL | Opt-in preference/fact extraction code in `handlers.py` and database; `test_user_profile_web_search.py`. | Local database/handler tests. | Tests passed. The repository explicitly does not automatically train a model or silently tune prompts. | None for the documented scope; do not describe this as autonomous model training. |
| Self-evolution and approval workflow | PARTIAL | Proposal, sandbox, hash review, and separate approval code in `handlers.py` and database; `tests/test_omega_mission_evolution.py`. | Local hash, authorization, patch policy, sandbox-redaction tests. | Tests passed. Deployment approval records a decision; it does not deploy a release. | A separate trusted production release mechanism and verified recovery procedure are still absent. |
| Mission Control and bounded background tasks | PARTIAL | `services/mission_control.py`, `services/agents.py`, runtime maintenance; `tests/test_omega_mission_evolution.py`. | Temporary database, encryption/scope, restart recovery, budget, and concurrency tests. | Tests passed locally. | Verify long-running jobs and persistence on the actual deployed checkout. |
| Staged ZIP workflow | CURRENT LOCAL IMPLEMENTATION | `services/telegram_zip_updates.py`, active `/update` handlers and review callbacks, and `tests/test_telegram_zip_updates.py`. Uploaded ZIPs are scanned, merged into a full candidate (omissions are preserved), tested in isolation, then bound to a digest for review. | Current repository tests include malformed ZIP, traversal, symlink, protected paths, staging isolation, authorization, replay, hash-tamper, patch-merge, and sandbox cases. | Local tests only; scanner daemon and real Telegram behavior were not exercised. | Review state is process-local and must be recreated after a bot restart. |
| Versioned production ZIP deployment | IMPLEMENTED LOCALLY; NOT LIVE-VERIFIED | `deploy/telegram_ai_deploy.py` creates a verified snapshot, immutable versioned release and atomic `current` pointer switch for its fixed service/path. | Local mocked deployment tests cover allowlist, exact hashes, capacity, snapshot creation, activation, health failure, rollback, and interrupted recovery. No deployment was attempted. | The broker is disabled by default. Windows mocks are not Linux/systemd evidence. | Independently verify the actual target, then run a disposable Linux/systemd recovery drill before any production enablement. |
| Deployment health check and rollback | IMPLEMENTED LOCALLY; NOT LIVE-VERIFIED | The broker waits for a new active PID and fresh marker for release import origin, DB readiness, and Telegram bot identity; failure triggers verified pointer restoration and recheck. | Local mocked tests exercise candidate-health failure, rollback, and interrupted recovery. | No real service restart or recovery drill was run. | Verify marker and systemd behavior on a compatible disposable Linux host; production remains NO-GO. |
| Database migrations, backups, and recovery | PARTIAL | `storage/database.py` includes migrations and backup-related code; tests cover temporary databases and encrypted backup/restore behavior. | Local tests only; production database was not opened or changed. | Local paths passed tests. No production backup/restore drill was performed. | Confirm the database path and perform an operator-approved restore drill from a verified backup. |
| Service startup and live runtime readiness | PARTIAL | Local service definition: `deploy/telegram-ai.service`; installer: `deploy/install_ubuntu.sh`. VM read-only `systemctl show` query found an active process, but a different working directory, user, and entry point. | `systemctl` metadata, restart count, Python version, and recent journal priority counts. | VM service was active/running with Python 3.12.3; cumulative restart count was 13,968; zero error and zero warning journal records were returned for the preceding hour. Application-level readiness was not tested. | Identify the intended checkout/service before claiming the workspace code is running on the VM. Investigate the restart count and run an approved app-level health check. |
| Documentation and declared feature limits | PARTIAL | `docs/FEATURES.md` and feature-specific docs distinguish implemented, partial, configuration-dependent, planned, and absent capabilities. | Documentation cross-check against source and tests. | Documentation is explicit about many boundaries; the live VM/workspace mismatch is not resolved by the docs. | Reconcile deployment documentation and runtime configuration with the actual VM service. |

## Test and check results

### Local automated suite

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

| Result | Count |
|---|---:|
| Passed | 154 |
| Failed | 0 |
| Skipped | 0 |
| Blocked | 0 |
| Not run in this suite | 0 |

The tests use temporary databases, constructed updates, mock transports, and mocked integrations. Passing them is not evidence of live Telegram/provider connectivity.

### Other checks

- `node.exe --check src\enan_ultra\webapp\static\app.js` — **passed** (exit code 0).
- `.venv\Scripts\python.exe -m ruff check --select E4,E7,E9,F src tests` — **not run**: Ruff is not installed in the selected virtual environment (`No module named ruff`). CI installs Ruff separately.
- CI wheel build/package-asset check and `compileall` — **not run** during this audit; the wheel build writes build artifacts, and this audit was limited to non-mutating checks.
- Live Telegram Bot API, AI providers, ClamAV, public Mini App, and application/database health checks — **5 scopes not run**. No test messages, paid requests, or production data operations were made.
- Azure VM read-only status query — **completed**, but it does not establish bot health. The active process is not running from the path or entry point configured by this repository.

## Critical findings and priority actions

1. **Rotate the disclosed credentials immediately.** The remote `.env` output exposed an OpenRouter key and Telegram API hash. Revoke/replace the OpenRouter key and reissue the Telegram application credential if possible. Do not copy the exposed values into tickets, chat, or logs.
2. **Resolve the VM/workspace mismatch.** The VM service runs `/home/azureenan/personal-telegram-ai/assistant.py` as `azureenan`, while this workspace's installer/unit target `/opt/enan-ultra` as `enan-ultra`. Until the intended code path is identified, do not claim this workspace's features are deployed.
3. **Investigate the cumulative 13,968 systemd restarts.** The service was active at query time and had no error/warning-priority journal records in the preceding hour, but the restart count is a significant operational warning.
4. **Keep ZIP production deployment disabled.** This repository verifies ZIP staging, not production activation, service restart, application health, or rollback.
5. **Run external smoke tests only after credential rotation and with explicit approval.** Prefer a dedicated test bot/provider account and non-sensitive test data; no live API calls were made in this audit.

## Commands used

Local:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node.exe --check src\enan_ultra\webapp\static\app.js
.\.venv\Scripts\python.exe -m ruff check --select E4,E7,E9,F src tests
```

Remote read-only checks used the supplied SSH identity and queried `systemctl show`, `systemctl is-active`, Python version, and recent journal record counts. The remote service reported:

```text
User=azureenan
WorkingDirectory=/home/azureenan/personal-telegram-ai
ExecStart=/home/azureenan/personal-telegram-ai/.venv/bin/python .../assistant.py
ActiveState=active
SubState=running
NRestarts=13968
Python=3.12.3
```

No source files, production files, production databases, or services were changed by the audit. The audit stops here pending the owner's next instruction.

## Follow-up: deployment target and blocker review (2026-10-17)

The operator confirmed that `enan-ultra.service` (`python -m enan_ultra`) is the intended package deployment target. All checks below were read-only; no production service, file, database, credential, or firewall was modified.

| Finding | Status | Evidence |
|---|---|---|
| Intended service identity | VERIFIED | `enan-ultra.service` was active/running as `azureenan`, working directory `/home/azureenan/Telegram-Bot-main`, executable `.venv/bin/python -m enan_ultra`, `NRestarts=0` at inspection. |
| Actual imported package | VERIFIED | The live interpreter resolved `enan_ultra` from `/home/azureenan/Telegram-Bot-main/.venv/lib/python3.12/site-packages/enan_ultra`, not the checkout's `src/` tree. The selected checkout has no Git metadata. |
| Canonical source revision | UNVERIFIED | Installed package `__main__.py`, `config.py`, `handlers.py`, `runtime.py`, and `storage/database.py` hashes do not match the local workspace; `services/telegram_zip_updates.py` is absent from the installed package. No authoritative VM commit or signed release artifact was established. |
| Safe broker compatibility | BLOCKED | The local broker expects `telegram-ai.service`, user `enan-ultra`, `/opt/enan-ultra`, and a root-owned, non-group/world-writable checkout. The selected VM service instead uses `/home/azureenan/Telegram-Bot-main`, user `azureenan`; the directory, package, and `.venv` are owner-writable (`0775`). The VM had no deployment helper, socket/service units, enable flag, backup root, or deployment history. Do not weaken the broker's checks. |
| Separate Telethon service failure | VERIFIED | `telegram-ai.service` runs `/home/azureenan/personal-telegram-ai/assistant.py`; its source calls `await client.start()`. The retained journal showed 19,633 `EOFError` process exits, 19,632 scheduled restarts, and traces into Telethon `auth.py`'s input prompt. systemd had `StandardInput=null`, `Restart=always`, `RestartSec=10`; `NRestarts=13968`. This service is not the operator-selected package target. |
| Telethon session-file permissions | VERIFIED — RISK | The separate Telethon session file exists with mode `0644`; its contents were not read. Treat it as sensitive authorization state and have the operator restrict access after confirming service ownership and recovery needs. |
| Current stability of package service | PARTIALLY VERIFIED | The package service was active with zero recorded restarts at inspection. That does not demonstrate application-level health, and no live Telegram/provider/database check was made. |
| Credential locations | VERIFIED (names only) | The selected package `.env` includes `OPENROUTER_API_KEY`, `OPENROUTER_API_KEYS`, and `TELEGRAM_BOT_TOKEN`; package source reads configuration through `src/enan_ultra/config.py`. The separate Telethon `.env` includes `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `OPENROUTER_API_KEY`; `assistant.py` reads those MTProto credentials. Values were neither read nor compared. |
| Local deployment safety tests | VERIFIED (mocked) | The existing deployment/ZIP tests cover layout rejection, protected files, manifest/archive hashes, unauthorized/replayed/expired approvals, mandatory validation, backup failure, concurrent deploy, interrupted activation, health/restart failure, and rollback. They use temporary directories and mocked systemd. |
| Running package import origin | VERIFIED | `enan-ultra.service` starts `python -m enan_ultra`, but that interpreter resolves the package from `.venv/lib/python3.12/site-packages/enan_ultra`, not from the checkout's `src` directory. |
| Local source manifest | VERIFIED (fingerprints only) | [DEPLOYMENT_SOURCE_MANIFEST.json](./DEPLOYMENT_SOURCE_MANIFEST.json) records local working-tree SHA-256 values for deployment/application source and the imported VM package fingerprints. It excludes `.env`, databases, credentials, and runtime state. Matching hashes are not a signature or proof of provenance. |
| Local Git/release provenance | PARTIALLY VERIFIED | Local `main` HEAD `eed00c9cd79fd926054f9103d28e9287d6b4d45f` matches remote `origin/main` and lightweight `v3.0`. The commit contains a signature, but local verification cannot validate it without the public key; there is no signed tag. The local branch's configured upstream `origin/upgrade/final-quality-gates` is not currently advertised by origin. `pyproject.toml` says `0.1.0`, unlike tag `v3.0`. |
| Trusted source / release artifact | BLOCKED | Current application/deployment source has uncommitted changes and no local wheel/release artifact was available. VM checkout has no Git metadata and its installed package fingerprints differ from the workspace. |
| Protected release migration | BLOCKED | A candidate plan is documented in [DEPLOYMENT_AND_ROLLBACK.md](./DEPLOYMENT_AND_ROLLBACK.md). The VM's home-directory code and `.venv` are writable by `azureenan`; that account belongs to the `sudo` group. `.env` is mode `0664`; three files exist under `data/`, including `DATABASE_PATH` at `/home/azureenan/Telegram-Bot-main/data/telegram_ai.sqlite3`. A dedicated non-login, non-sudo service account, root-owned releases, database backup/restore drill, and systemd rehearsal are prerequisites; none were performed. |
| Database-layer ZIP protection | VERIFIED (local test) | `src/enan_ultra/storage/database.py` is now in the ZIP deployment protected-path policy; an isolated test asserts such a release fails before systemd restart. |

### Required remediation sequence

1. Keep `UPDATE_DEPLOYMENT_ENABLED=false`; do not install the broker, create its enable flag, or run a production deployment.
2. The operator must provide/identify the authoritative source commit or signed artifact for `/home/azureenan/Telegram-Bot-main` and review differences from this workspace. Git metadata and provenance are absent from the observed directory.
3. In a separately approved maintenance plan, choose a root-owned, non-writable-by-service application path and reconcile the selected `enan-ultra.service` unit, account, Python environment, environment-file loading, database path, and persistent data. Do not use the service-account-writable `0775` home checkout as a privileged helper target. Preserve service identity only if the operator explicitly confirms that choice.
4. Build a dedicated fixed broker profile for the reviewed target only after the canonical artifact and root-owned layout exist. Keep exact service/path/owner checks; validate the broker in a disposable Linux/systemd environment, including backup, health, failure and rollback cases, before operator review of any live enablement.
5. Separately remediate the non-target Telethon service: one-time authentication/session creation must occur only through an explicitly authorized operator flow, not systemd startup. Use a private session file owned by its service account; at service boot check that the session exists and is authorized, use a non-interactive connection path, and fail with a clear error rather than invoking phone/password prompts. No session content was inspected.
6. Revoke the previously exposed OpenRouter key and provision a replacement in each confirmed consumer's protected configuration. Do not assume values in different `.env` files are the same key.
7. For the exposed Telegram MTProto API hash, verify the supported remediation with Telegram through the registered app/support channel before changing it; do not assume that reissuing it is available or that changing it invalidates existing sessions. Review/revoke compromised Telethon authorization sessions through the supported operator flow if exposure is suspected. The current package service uses the Bot API token, not the MTProto `API_HASH`.

Focused deployment/ZIP local tests after this follow-up: **45 passed, 0 failed, 0 skipped**. Complete local suite after this follow-up: **176 passed, 0 failed, 0 skipped**. Ruff, Python compile, frontend syntax, source-manifest hash verification, and `git diff --check` passed. These are local/mock results, not Azure integration evidence.
