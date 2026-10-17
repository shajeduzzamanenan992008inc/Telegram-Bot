# ENAN ULTRA Bug Root Cause Report

## Audit scope

- Audit date: 2026-10-17.
- Workspace: `D:\FTP\Telegram-Bot`.
- Audit base branch and commit: `codex/admin-zip-update`, `b989627` (`b989627ae44f3f0177a6d1a87069d989d591f712`). The supplied baseline `4448a84786fe88afaf9ea067a0aca4742ba46184` is an ancestor, not the audit base `HEAD`.
- Git was clean before this report was created. Existing ignored staging files were left untouched.
- No `.env` contents, API keys, Telegram tokens, Azure state, production database, service state, firewall, or production files were read or changed.
- Baseline verification on this workspace: `uv run --locked --python 3.15 pytest -q` — **181 passed in 80.98s** on Windows/Python 3.15. No live Telegram, Linux, systemd, ClamAV daemon, or production integration test was performed.

Findings below are based on current source and test coverage. Runtime-only causes remain unconfirmed unless stated otherwise.

## Confirmed findings

### BUG-001 — Admin command names appear in the public command menu

- **Severity:** P1 (administrative information exposure; backend authorization remains present).
- **Reproduction:** Call `BotHandlers.register_commands()` and inspect the default-scope command list passed to `set_my_commands()`.
- **Root cause:** `src/enan_ultra/handlers.py` puts `/update`, `/block`, and `/unblock` in the default bot command list. `runtime.py` separately installs a chat-scoped admin list, but a scoped list does not remove entries from the default list for other chats.
- **Authorization evidence:** `/update` checks `user.id == settings.admin_id`; `/block` and `/unblock` call `_gate(..., admin=True)`. No privileged operation was observed to rely only on the hidden menu.
- **Affected files/functions:** `src/enan_ultra/handlers.py::register_commands`, `update`, `block_user`, `unblock_user`; `src/enan_ultra/runtime.py::post_init`.
- **Proposed fix:** Remove privileged commands from the default list, keep them in administrator scopes, and test the default and admin scopes independently.
- **Regression tests:** Default scope excludes every admin-only command; an administrator scope includes the intended admin commands; non-admin calls still fail backend authorization.
- **Production confirmation:** Telegram command scopes were not queried.

### BUG-002 — The active `/update` path bypasses the staged approval workflow

- **Severity:** P0 (unreviewed archive contents can replace live application code).
- **Reproduction:** Send `/update` with a ZIP as the configured `ADMIN_ID`, or send a ZIP document to that account. The `update()` command and `on_message()` document route both call `_receive_update_zip()`.
- **Root cause:** `_receive_update_zip()` downloads to the shared `/tmp/bot_update.zip`, optionally scans only when `MALWARE_SCAN_ENABLED` is true, validates with `zipfile.is_zipfile()`, and calls `extract_update_archive()` against `Path.cwd()`. It then sends a restart message and runs `os.execv()`.
- **Security gaps:** Scanner-off mode accepts an unscanned package; there is no explicit review/approval, verified candidate build, deployment backup, health check, or rollback after process replacement. Errors delete the archive in `finally`, so scanner outage does not retain it in quarantine. Atomic per-file replacement only rolls back Python exceptions during extraction; process interruption or failed restart can leave a mixed or changed live tree.
- **Dead-code evidence:** `TelegramZipUpdateManager` provides `start_upload()` and `prepare()`, but no current Telegram handler calls either. `update_callback()` has no matching `CallbackQueryHandler` registration in `BotHandlers.install()`. Existing tests exercise manager and callback methods directly, not the active upload-to-approval route. No test covers `telegram_direct_update.extract_update_archive()` from the Telegram handler.
- **Affected files/functions:** `src/enan_ultra/handlers.py::update`, `_receive_update_zip`, `install`, `update_callback`; `src/enan_ultra/services/telegram_direct_update.py`; `src/enan_ultra/services/telegram_zip_updates.py`.
- **Proposed fix:** Route `/update` uploads into a unique quarantine, require a successful ClamAV scan, validate and stage the exact archive, run isolated tests, and expose the existing admin review/approval callbacks through registered handlers. Keep activation behind the existing disabled-by-default broker; do not restart production from the bot upload handler.
- **Regression tests:** scanner clean/infected/unavailable/timeout; quarantine retention; unsafe ZIP rejection; no live-tree changes before approval; callback registration and authorization; simulated success/failure/rollback; no production service calls.
- **Production confirmation:** Actual scanner, flag, broker/socket, service, and state status are unknown.

### BUG-003 — Scanner error text cannot identify the underlying runtime failure

- **Severity:** P1 (uploads fail closed, but diagnosis is too broad).
- **Reproduction:** With scanning enabled, make ClamAV's Unix socket absent, time out a response, or return an unexpected protocol response. `scan_upload()` raises `AntivirusUnavailable`, and `_upload_scan_message()` returns the same user-facing message for all such cases.
- **Root cause:** The adapter intentionally treats unavailable, timed-out, truncated, and invalid replies as unavailable, but the public/admin status does not include a safe diagnostic category or scanner health result.
- **Evidence:** `services/antivirus.py::scan_upload` fails closed for socket/timeout/protocol errors. `deploy/install_ubuntu.sh` installs and starts ClamAV for a new deployment config and turns scanning on when the setting is absent; an existing explicit setting is preserved. `.env.example` itself defaults scanning off.
- **Affected files/functions:** `services/antivirus.py::scan_upload`; `handlers.py::_upload_scan_message`, `_download`, `_receive_update_zip`; `deploy/install_ubuntu.sh`.
- **Proposed fix:** Add a read-only, secret-free scanner health diagnostic and distinguish malware-positive, not-installed/unreachable, timeout, and invalid-response categories in admin diagnostics. ZIP updates must always require scan success regardless of the general-upload setting.
- **Regression tests:** Clean, infected, missing socket, timeout, malformed reply, and admin-safe diagnostic mapping.
- **Production confirmation:** **Not confirmed.** No approved host/log access was available; the exact current error could be caused by any adapter-unavailable path above.

### BUG-004 — Configured Ollama is not part of automatic normal-mode fallback

- **Severity:** P1 (provider-selection behavior differs from local-first fallback expectation).
- **Reproduction:** Configure `OLLAMA_BASE_URL` and `OLLAMA_MODEL`, leave `DEFAULT_AI_MODEL=auto`, and issue a normal non-local chat. `ProviderRouter._chain()` builds candidates from the cloud-only `AI_PROVIDER_ORDER`; it adds Ollama only for `local_only=True`. `_provider_order()` explicitly rejects `ollama`.
- **Root cause:** The code supports Ollama as an adapter and accepts an explicit `ollama::model` primary, but does not automatically prefer a configured local adapter in ordinary mode. Existing `test_provider_fallback_and_local_only_never_call_cloud` explicitly expects Ollama to be skipped in normal mode.
- **What is already working locally:** Fallback across configured cloud providers is ordered, per-request attempt-bounded, timed, and circuit-aware; provider errors are sanitized. Existing tests cover fallback, rate limits, credential rotation, cost gates, and local-only isolation.
- **Affected files/functions:** `src/enan_ultra/config.py::_provider_order`, `Settings.load`; `src/enan_ultra/services/providers.py::ProviderRouter.default_model`, `_chain`, `complete`; `tests/test_final_upgrade.py::test_provider_fallback_and_local_only_never_call_cloud`.
- **Proposed fix:** When Ollama is configured and the user has not explicitly selected a provider, try its configured model first in normal mode, then the configured cloud order. Preserve strict local-only behavior and explicit model selection. Add outage and unconfigured-local cases.
- **Regression tests:** Local success; local timeout then API success; missing local configuration goes directly to API; local-only never calls cloud; no duplicate provider attempts beyond configured fallback models.
- **Production confirmation:** Actual model, provider keys, provider reachability, and operator selection were not inspected. No claim is made about why production AI replies stopped.

### BUG-005 — Telegram update processing has no durable update-ID deduplication

- **Severity:** P1 (duplicate webhook/replayed update side effects are possible).
- **Reproduction:** Submit the same `Update.update_id` twice through the application; no database or handler guard currently claims that ID before command/message handlers run.
- **Root cause:** `runtime.py` enables `concurrent_updates(16)`. `_chat()` uses an in-process per-chat lock and `_request_allowed()` uses an in-memory per-user cooldown, but neither records a Telegram update ID durably. `main()` starts polling only; no webhook startup path is configured. A second process or duplicate delivery is not blocked by a shared idempotency record.
- **Handler behavior:** Command messages are not ordinarily handled twice by the command and general-message handlers because the general handler skips slash commands. The special document-update dedup set only covers one in-process `/update` handler overlap.
- **Affected files/functions:** `runtime.py::build_application`; `handlers.py::install`, `on_extension_update`, `_chat`, `_request_allowed`; `storage/database.py` schema and operations; `__main__.py::main`.
- **Proposed fix:** Claim each `update_id` atomically in the shared SQLite database before any update-specific side effect; reject duplicate claims using PTB's stop-processing signal; bound retention and test concurrent replays.
- **Regression tests:** Concurrent duplicate IDs produce one side effect; duplicate after handler completion produces no second reply; unique IDs remain unaffected; claim-store outage fails safely and is logged without secrets.
- **Production confirmation:** Number of running bot instances, Telegram polling conflicts, and any external webhook configuration are unknown.

### BUG-006 — Default deployment database path conflicts with the systemd write boundary

- **Severity:** P1 (a fresh install using the shipped defaults may fail to persist state).
- **Reproduction:** The unit runs from `/opt/enan-ultra` with `ProtectSystem=strict` and `ReadWritePaths=/var/lib/telegram-ai`. The shipped `.env.example` sets `DATABASE_PATH=data/telegram_ai.sqlite3`, which resolves below `/opt/enan-ultra`, outside the declared writable state root. `install_ubuntu.sh` copies `.env.example` but does not install the ignored staging state drop-in.
- **Root cause:** The deployment default and state-directory write policy are inconsistent. The separate staging example sets `/var/lib/telegram-ai/telegram_ai.sqlite3`, but it is not installed by the normal installer.
- **Affected files:** `.env.example`, `deploy/telegram-ai.service`, `deploy/install_ubuntu.sh`, `deploy/staging/*` (local ignored staging examples), `config.py` database path handling.
- **Proposed fix:** Make the actual installer-generated service configuration put `DATABASE_PATH` under `/var/lib/telegram-ai`; update the path regression test and deployment docs. Keep developer-local defaults explicit and do not copy existing production state.
- **Regression tests:** Effective sample/installer config resolves database and runtime health marker under the unit's writable path; no production migration or service start.
- **Production confirmation:** No unit was loaded and no production database path was inspected.

## Areas with no confirmed code root cause in this audit

### Sticker collection and sending

Collection is deliberately limited to configured administrators' private messages and is disabled until `/collect_on`; collected entries are unapproved until review. The bot stores sticker metadata rather than the surrounding message text, deduplicates via the catalog, and inline search exposes approved stickers only. Normal users' stickers are not collected. That is a privacy-safe scope, not evidence of an unexplained collection defect. Sending uses approved catalog entries, per-user preferences/cooldowns, and catches Telegram failures so the text reply remains available. Existing `tests/test_sticker_collector.py` covers opt-in, deduplication, import, approval, send failure, and metadata migration. Real Bot API sendability and production preferences are unverified.

### Emoji and Mini App themes

Emoji decoration is a local presentation step with conservative size, code, URL, JSON, and serious-context exclusions. Theme decisions are persisted per user; the Mini App applies CSS variables, light/dark appearance, palettes, canvas animation, and reduced-motion settings without a reload after theme API updates. Python tests cover theme scoring, persistence, mode, favorites, Mini App auth/isolation, and streamed theme changes. CI only syntax-checks `app.js`; there are no browser-level visual/accessibility tests in this workspace. No current runtime complaint can be tied to a reproducible source failure without the user's exact preference/state and a Mini App session.

## Outstanding evidence required

- Safe runtime configuration names only (never values), provider diagnostics without health requests that incur cost, and redacted logs for a reported outage.
- Read-only scanner daemon/service/socket health and redacted scanner error category.
- Confirmation of one intended bot process and whether any external webhook/multiple worker is configured.
- Disposable Linux/systemd staging on Python 3.12, real ClamAV, rootless test sandbox, state-path verification, consistent backup/restore, activation interruption, health failure, and rollback. These must use staging-only data and credentials.
- Approved Azure/host identity and access route before any live inventory. No live change is requested or authorized by this report.

## Phase status

This is the Phase 1 source-and-test audit. It records proposed fixes, not completed fixes. Keep production **NO-GO** until scanner operation, source/commit provenance, effective service/state paths, Linux staging, backup/restore, and rollback are independently verified.

## Local remediation outcomes — 2026-10-17

The findings above describe the verified pre-change source. The following fixes are now applied locally and covered by regression tests; they have not been deployed.

| Finding | Local change and verification | Remaining limit |
|---|---|---|
| BUG-001 | Removed `/update`, `/block`, and `/unblock` from the default command list; added an admin-scoped command-menu helper; `/update` and its review callbacks require the exact configured `ADMIN_ID`, while block/unblock retain `_gate(admin=True)`. Tests check menu scopes and reject non-admin actions. | Telegram command scopes have not been queried from a live bot. |
| BUG-002 | Removed the live-tree extractor/restart path from the active Telegram handler. Admin ZIPs now enter a unique mode-0700 quarantine with mode-0600 files; every update ZIP must scan clean regardless of the general upload flag. The handler records SHA-256 metadata, validates with `zipfile.is_zipfile()` and the staged manager, runs its sandbox check, and registers review callbacks. Scanner/validation failures keep the archive quarantined; old quarantine files expire after 24 hours. Staging tests prove the live project tree is unchanged. | The review manager is process-local and restart cleanup removes orphaned staged releases. Bot-triggered deployment is not activated or tested here; production activation, backup/health gates, and rollback still require isolated Linux staging. |
| BUG-003 | `AntivirusUnavailable` now carries safe categories for missing configuration/socket, unreachable socket, timeout, incomplete response, and invalid response. ZIP admin messages and logs include the category without exposing credentials; the scanner still fails closed. | Only mocked scanner behavior was tested. The actual ClamAV daemon/socket and the production error cause remain unverified. |
| BUG-004 | When `DEFAULT_AI_MODEL=auto`, a configured Ollama adapter is selected first. Normal routing falls through to configured cloud order after local failure; explicit model choice and local-only isolation remain intact. Regression tests cover local success, Ollama-to-Groq fallback, missing Ollama, and local-only. | Provider credentials, connectivity, cost policy, and production model selection were not inspected. |
| BUG-005 | `BotHandlers.install()` is idempotent for the same object/application and rejects repeated registration by another instance on the same app. A new SQLite `processed_updates` table atomically claims Telegram update IDs before handlers run; duplicate or unclaimable updates stop before side effects. The table is pruned after 30 days. | The application uses polling only in source, but the number of production processes and any external webhook remain unknown. A crash after claiming and before replying is at-most-once and may drop that reply. |
| BUG-006 | The Ubuntu installer rewrites the shipped relative DB default to `/var/lib/telegram-ai/telegram_ai.sqlite3`, adds the path when absent, preserves an operator-provided in-state absolute path, and rejects paths outside the systemd writable boundary. | Static regression test only; no Linux installer or service was run. |

The generic message handler explicitly skips slash-prefixed message text, so it was not a duplicate command route in the inspected source. It also skips the same incoming `update_id` after a durable claim. Neither fact confirms what happened on a production host with unknown process/webhook state.

Sticker collection and theme rendering have no confirmed source root cause in this audit and were not changed. Existing test coverage remains in place; browser-level visual/accessibility checks and real Telegram sticker sending remain unverified.

### Verification results

- Full local test suite on Windows/Python 3.15 after all code and test changes: `uv run --isolated --locked --python 3.15 --group test pytest -q` — **191 passed in 75.70s**.
- Full local suites before the final caption-routing regression: **190 passed** on each of Python 3.11, 3.12, 3.13, and 3.14. After the final ZIP route and quarantine changes, the three ZIP caption/scan/staging regressions passed on each of those four versions.
- `uvx --from ruff==0.17.1 ruff check --select E4,E7,E9,F src tests`: PASS.
- `python -m compileall -q src tests`, `node --check src/enan_ultra/webapp/static/app.js`, and `uv build --wheel --no-sources`: PASS.
- The full GitHub check details supplied by the user returned 404 from this workspace, so their original failure trace could not be confirmed. No claim is made that the older run's failure cause is known.
- One non-isolated Python-matrix retry selected the existing Python 3.12 environment without project dependencies and stopped at collection with 16 `ModuleNotFoundError` errors and zero tests executed. Rerunning through `uv run --isolated` resolved the environment issue; the final Python 3.15 full suite passed as recorded above.

Production remains **NO-GO** pending real scanner, Telegram, Linux/systemd, state, backup/restore, and rollback evidence.
