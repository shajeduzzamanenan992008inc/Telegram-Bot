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

## CI failure root cause (2026-11-25)

Every failed job at `446fcae` (Telegram Bot CI, Tests, CodeQL, Configuration
Validation) has zero executed steps and this GitHub annotation:

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased.

That is an account billing/plan blocker, not a repository defect. GitHub Actions
cannot run and CodeQL/configuration results cannot be produced until it is
resolved. One additional real failure was reproduced locally and fixed:
`pip-audit` found 9 vulnerabilities in `pypdf 6.21.2`, `soupsieve 3.0.3`, and
`opentelemetry-api 1.45.1`. Pins/lock now resolve `pypdf 6.21.4`,
`soupsieve 4.1.3`, and `opentelemetry-api 1.45.2`; the audit reports no known
vulnerabilities.

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
| Dependency audit | no known vulnerabilities |
| Python 3.11 pytest | 322 passed |
| Workflow/JSON YAML parse | passed |

## CI workflow repair

`ci.yml` was an OGX pipeline expecting `GUARDIAN-X/` and an `ogx` package. It now
covers this repository: Python 3.11–3.15 pytest matrix, ruff, compile checks,
JavaScript syntax/tests, dependency audit, wheel build, Mini App asset
verification, and configuration validation. `config-validation.yml` now validates
this repository's workflows/JSON/settings. CodeQL and dependency review remain
configured. Workflows have **not** been executed on GitHub from this workspace.

## GitHub settings (verified via API)

- Branch protection on `main` is now active: strict required checks
  (10 contexts), 1 approving review, force pushes and branch deletion disabled.
  Admin enforcement is off so an owner can land an emergency fix.
- Dependabot alerts and automated security fixes enabled.
- Secret scanning: GitHub returned "Secret scanning is not available for this
  repository" (plan/GHAS limitation), so it is not active.
- Dependabot PR #15 (CodeQL action 3.39.4 → 4.39.4) is **closed**; there are zero
  open pull requests, so nothing was merged.

## Not completed / not verifiable here

- GitHub Actions execution: blocked by the account billing/spending-limit
  annotation, not by a code defect. CodeQL and Configuration Validation results
  therefore remain unverified.
- Live deployment, systemd install, backup/restore, and production smoke tests:
  not performed (no authorized target host).
- Background-task mode enforcement: no user-facing background authorization path
  was found to test.
- Webhook mode: the bot uses polling; no webhook infrastructure exists to test.

## Status

**BLOCKED** — all reproducible code defects are fixed and verified locally (322
tests on Python 3.11 and 3.15, clean static analysis, clean dependency audit,
wheel and assets verified), and branch protection is active. Priority 1 cannot
be closed because GitHub Actions jobs are refused before execution by the
account's failed payment/spending limit; CodeQL and CI cannot go green until the
owner resolves billing. Secret scanning is unavailable on the current plan.
