# Final Engineering Report — Private/Public Mode Remediation

- **Repository**: https://github.com/shajeduzzamanenan992008inc/Telegram-Bot.git
- **Reviewed base commit**: `936d0e5d3c3f8a1275f128d80369bf1a86960cdd`
- **Date**: 2026-11-25
- **Package**: `enan-ultra-telegram-ai` v0.1.0

## Critical findings and fixes

### 1. Private-mode bypass (CRITICAL) — fixed

`AccessControlService.is_access_allowed()` returned `allowed` when
`settings.access_mode == "public"` before consulting the database. Because the
shipped default is `ACCESS_MODE=public`, a database PRIVATE mode could be bypassed
for ordinary users.

Now:

1. administrators are allowed;
2. the database mode is read and validated (failure → `mode_unavailable`, denied);
3. blocked users are denied;
4. PRIVATE denies all ordinary users;
5. PUBLIC applies the existing `is_authorized` policy (`public` or allowlist);
6. local `ACCESS_MODE` can never override the database.

### 2. Global mode row never created (CRITICAL) — fixed

Migration 17 seeded `global_access_mode` with an unbound `?` placeholder, so the
table stayed empty and `UPDATE ... WHERE id=1` changed nothing. `/setmode public`
was a silent no-op. The migration now uses `CURRENT_TIMESTAMP`, and
`set_global_access_mode()` performs an upsert so a missing row cannot mask future
changes.

### 3. Callback and Mini App bypasses (HIGH) — fixed

- `menu_callback()` only used `is_authorized()`; it now calls
  `is_access_allowed()`, so callback buttons respect PRIVATE.
- `MiniAppServer` had lost `_authenticate()` in the latest commit and defined a
  second `__init__` that referenced an undefined `theme_engine`, making the Mini
  App constructor fail. The duplicate initializer was removed and
  `_authenticate()` restored, with `is_access_allowed()` on every request.

### 4. `/setmode` collision (HIGH) — fixed

The command dictionary registered `setmode` twice; the second entry
(`set_global_mode`) shadowed the chat-mode handler. A single registration now
dispatches: `private|public` → admin-only global mode; chat styles → per-chat
mode.

### 5. Lint/runtime defects — fixed

- Duplicate `AccessControlService` import in `handlers.py`.
- Undefined `theme_engine` and duplicate `__init__` in `mini_app.py`.
- Unused `shutil` import in `scripts/init_env.py`.
- Missing `chatmode`-related menu drift resolved without exceeding Telegram's
  100-command-per-scope limit by keeping `/setmode` as the dispatcher.

## Restored regression coverage

`tests/test_final_upgrade.py` had been reduced to 3 lines. It was restored from
`47067e7` (1,650 lines) and reconciled with current code:

- schema-version assertions updated to 17;
- tests that predate global modes now explicitly set the database to PUBLIC;
- the fake database in the privacy-export test implements the mode read.

Result: 45 tests in that file, all executing and passing. No test was deleted or
weakened to obtain a pass.

## Verified test and build results

| Check | Result |
| --- | --- |
| Full pytest suite | 322 passed, 0 failed |
| Restored `test_final_upgrade.py` | 45 passed |
| Mode tests (`test_private_public_mode.py`, `test_private_public_enforcement.py`) | 27 passed |
| Ruff `E4,E7,E9,F` | clean |
| `compileall` (src, tests, scripts) | clean |
| Node syntax + tests | clean (3 tests) |
| Wheel build | 62 files, assets verified |
| Config validation | passed (138 documented keys) |
| Workflow/JSON YAML parse | passed |

## CI workflow repair

`ci.yml` was an OGX pipeline expecting `GUARDIAN-X/` and an `ogx` package. It now
covers this repository: Python 3.11–3.15 pytest matrix, ruff, compile checks,
JavaScript syntax/tests, dependency audit, wheel build, Mini App asset
verification, and configuration validation. `config-validation.yml` now validates
this repository's workflows/JSON/settings. CodeQL and dependency review remain
configured. Workflows have **not** been executed on GitHub from this workspace.

## Not completed / not verifiable here

- Dependabot PRs #15–#19: no GitHub access in this environment; none inspected,
  merged, or closed.
- Branch protection, required reviews, secret scanning, and CodeQL results: no
  GitHub administrative access; not claimed as active.
- Live deployment, systemd install, backup/restore, and production smoke tests:
  not performed (no authorized target host).
- Background-task mode enforcement: no user-facing background authorization path
  was found to test.
- Webhook mode: the bot uses polling; no webhook infrastructure exists to test.

## Status

**COMPLETE WITH LIMITATIONS** — the critical Private Mode bypass and the mode-row
migration defect are fixed and covered by real tests; CI and documentation are
repaired. GitHub-side PR/settings verification and live deployment remain open.
