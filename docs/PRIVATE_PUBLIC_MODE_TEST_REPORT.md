# Private/Public Mode Test Report

Verified locally on Windows, Python 3.15.2, on 2026-11-25.

## Result

- **Full pytest suite**: `322 passed` (`python -m pytest -q`), 0 failed.
- **Restored regression file**: `tests/test_final_upgrade.py` — 45 tests, all
  passing (file restored from commit `47067e7`, reconciled with current APIs).
- **Mode-specific tests**: 27 passing across:
  - `tests/test_private_public_mode.py` (21)
  - `tests/test_private_public_enforcement.py` (6)

## What is covered

### Real database and real service

`tests/test_private_public_mode.py` builds `Settings` through the real loader and
`Database` in a temporary directory, then uses the real `AccessControlService`:

- PRIVATE database mode + `ACCESS_MODE=public` → ordinary and allowlisted users
  denied `private_mode`; admin allowed.
- PRIVATE database mode + `ACCESS_MODE=allowlist` → same denial.
- PUBLIC database mode + `ACCESS_MODE=public` → allowed; blocked user denied.
- PUBLIC database mode + `ACCESS_MODE=allowlist` → only allowlisted user allowed.
- Unreadable mode → `mode_unavailable`; admin still allowed; `require_access`
  raises with that reason.
- Unrecognized persisted mode → fail closed.
- Fresh database defaults to PRIVATE.
- Mini App `initData` signature validation plus global-mode re-check.

### Entry points and concurrency

`tests/test_private_public_enforcement.py`:

- `/setmode` dispatcher: `/setmode public` changes the global mode,
  `/setmode sarcastic` changes the chat mode without touching the global mode,
  and a non-admin cannot change the global mode.
- `_gate()` denies ordinary commands/messages in PRIVATE and restores them in
  PUBLIC.
- `menu_callback()` denies ordinary callback queries in PRIVATE and serves them
  in PUBLIC.
- Two `Database` connections on one file observe each other's mode changes.
- Concurrent PRIVATE/PUBLIC changes leave a valid mode and matching decisions.
- Mode-read failure denies ordinary users and keeps admin recovery access.

### Migrations

`tests/test_final_upgrade.py` asserts the final schema version is 17 and that the
`global_access_mode` table is seeded. The seeding bug (unbound placeholder) is
covered indirectly by every test that switches to PUBLIC and reads it back.

## Not covered / limitations

- Workflows have not been executed on GitHub from this environment; only local
  equivalents (pytest, ruff, compileall, Node checks, wheel build) were run.
- No webhook transport test infrastructure exists; the bot uses polling.
- Long-running background jobs are not exercised against a mode change; the
  codebase has no user-facing background authorization path beyond handler
  gates.
- No browser/E2E test for the Mini App UI.
- No Dependabot PR or branch-protection verification is possible locally.

## Commands used

```bash
python -m pytest -q
uvx ruff@0.17.1 check --select E4,E7,E9,F src tests scripts
python -m compileall -q src tests scripts
node --check src/enan_ultra/webapp/static/app.js
node --test tests/test_webapp_theme_ui.cjs
uv build --wheel --no-sources
PYTHONPATH=src python scripts/validate_config.py
```
