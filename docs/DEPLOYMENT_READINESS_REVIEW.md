# Deployment Readiness Review — Telegram-Bot

> **Scope note.** The review body was written on 2026-11-25 against an earlier checkout. Its command results and GitHub-side statements were re-checked on 2026-11-27 and corrected below; the original figures (`322 passed`, `138` keys, `62` wheel files, schema `17`) belong to that checkout.

Reviewed 2026-11-25. This review distinguishes locally verified checks from
items that require GitHub or a target host.

## Verified in this workspace

- Full pytest suite: **88 passed** across 11 modules.
- Ruff `E4,E7,E9,F`: clean across `src`, `tests`, `scripts`.
- `python -m compileall -q src tests scripts`: clean.
- `node --check` and `node --test`: clean (15 JavaScript tests in
  `tests/test_webapp_theme_ui.cjs`).
- Wheel build: `enan_ultra_telegram_ai-0.1.0-py3-none-any.whl`, 64 files.
- All Mini App assets present in the wheel (3 static files + 10 theme JSONs).
- `scripts/validate_config.py`: `.env.example` documents 146 unique keys; the
  Settings loader accepts documented defaults and rejects invalid
  `ACCESS_MODE`/allowlist combinations.
- Workflow YAML and repository JSON parse successfully.

## Linux (Ubuntu/Debian) — configured, not deployed here

- `deploy/telegram-ai.service`, `deploy/telegram-ai-deploy.service`/`.socket`,
  and `deploy/install_ubuntu.sh` are present.
- Deployment broker and health-check/rollback code are present; no live host was
  touched in this review.
- systemd units and installer were **not executed** on a Linux host here.

## Windows

- Python 3.15 test run succeeds locally.
- No native service installer; POSIX permission calls are skipped.
- Named-pipe paths for the deployment broker/ClamAV are configurable but were not
  exercised against a Windows service/daemon in this review.

## Database and migrations

- SQLite WAL mode, foreign keys, 5-second busy timeout.
- Migrations are forward-only and idempotent; highest schema version is **16**.
- `global_access_mode` lives in the `settings` table and defaults to `private`.
- Restore/rollback of a production database was **not** executed here.

## Configuration

- `.env.example` has 146 keys and is checked against literal keys referenced by
  `config.py`.
- Required secrets: `TELEGRAM_BOT_TOKEN`, `ADMIN_CHAT_ID`, `ADMIN_USER_IDS`,
  `APP_DATA_KEY`, `DASHBOARD_AUTH_TOKEN`, plus at least one provider credential.
- `ACCESS_MODE` defaults to `public` but cannot override the persisted PRIVATE
  mode.

## Health, rollback, and security

- `/healthz` exists on the Mini App; dashboard health snapshots exist.
- Provider circuit breakers, admin alerts, encrypted backup and audit-chain
  verification are implemented.
- Dependency audit passes locally with no known vulnerabilities after bumping
  `pypdf`, `soupsieve`, and `opentelemetry-api`.
- All triggered workflows pass on `main` at `e0944e4` (19 successful check runs
  across Tests, Telegram Bot CI, Telegram Bot Code Security and CodeQL Advanced;
  GitHub Configuration Validation is path-filtered and skips docs-only changes);
  0 open code-scanning alerts.
- Branch protection on `main` is **enabled** through the `protect-main` ruleset
  (active, targets the default branch): pull requests required, deletions and
  force pushes blocked, 15 required status checks enforced (CI pytest matrix,
  quality, dependency audit, wheel/Mini App assets, configuration validation, and
  the Tests workflow `quality` + `test` matrix). No approving review is required,
  so the sole owner can still merge a green pull request. Checked via the GitHub
  API on 2026-11-27. Secret scanning is available; its single historical alert is
  resolved (`revoked`). Dependabot alerts: 0 open; no pull requests are open.

## Verdict

**Locally verified for test/static/build readiness.** Linux deployment remains
conditional on executing the deployment runbook on an authorized host. CI is
green on `main` and branch protection is enforced by the `protect-main` ruleset.

## Open items

| Item | Status |
| --- | --- |
| Run CI workflows on GitHub | Pass on `main` at `e0944e4` (19 checks) |
| Branch protection / required checks | Enforced by the `protect-main` ruleset — 15 required checks, 0 approving reviews |
| Dependabot alerts | 0 open |
| Migration rollback test | Not performed |
| Live Telegram Bot API smoke test | Not performed here; one-command verifier ready — `scripts/smoke_deployment.py`, see [live deployment verification](LIVE_DEPLOY_VERIFICATION.md) |
| Linux systemd install/restart | Not performed here; run `deploy/install_ubuntu.sh`, then the verifier, on the host |
