# Release Checklist — Telegram-Bot

## Pre-Release Verification

### Code Quality (verified 2026-11-27)

- [x] Full pytest suite passes — 88 tests across 11 modules
- [x] Ruff (`E4,E7,E9,F`) clean across `src`, `tests`
- [x] Python compile check passes
- [x] JavaScript syntax check passes
- [x] JavaScript tests pass (15 in `tests/test_webapp_theme_ui.cjs`)
- [x] Wheel builds (`enan_ultra_telegram_ai-0.1.0-py3-none-any.whl`, 64 files)
- [x] Mini App assets verified in the wheel (3 static files + 10 theme JSONs)
- [x] Configuration validation (`scripts/validate_config.py`) passes

### Security

- [x] Private/Public bypass fixed: database mode precedes ordinary-user checks
- [x] Fail-closed behavior when the persisted mode cannot be read
- [x] Global mode persisted in `settings` (`global_access_mode`, default `private`); highest migration is 16
- [x] `/setmode` command collision removed
- [x] Mode-specific regression tests pass (`tests/test_security_and_access.py`, `tests/test_database.py`)
- [x] Dependency audit (`pip-audit`) — no known vulnerabilities
- [x] Dependabot alerts: 0 open
- [x] CodeQL Advanced runs on `main`; 0 open code-scanning alerts
- [x] Secret scanning available; the single historical alert is resolved (`revoked`)
- [ ] Branch protection on `main` — **not enabled**: no required checks, reviewer
      approval, force-push or deletion protection (checked via API 2026-11-27)

### Documentation

- [x] `docs/ACCESS_CONTROL_MODES.md`
- [x] `docs/PRIVATE_PUBLIC_MODE_SECURITY.md`
- [x] `docs/PRIVATE_PUBLIC_MODE_TEST_REPORT.md`
- [x] `docs/DEPLOYMENT_READINESS_REVIEW.md`
- [x] `docs/KNOWN_LIMITATIONS.md`
- [x] `docs/FINAL_ENGINEERING_REPORT.md`
- [x] `docs/RELEASE_CHECKLIST.md`

### Configuration

- [x] `.env.example` has 146 unique keys and matches literal loader keys
- [x] Required variables documented
- [x] Invalid `ACCESS_MODE` / empty allowlist rejected by the loader

### CI/CD

- [x] `ci.yml` targets this repository (Python 3.11–3.15 matrix, pytest, ruff,
      compile, JS, dependency audit, wheel, Mini App assets, config validation)
- [x] `config-validation.yml` validates this repository's YAML/JSON/settings
- [x] `security.yml` CodeQL configuration retained
- [x] `dependency-review.yml` retained
- [x] All five workflows pass on `main` at `b8f73ba`: Tests, Telegram Bot CI,
      GitHub Configuration Validation, Telegram Bot Code Security, CodeQL Advanced

## Release Process

1. **Version bump**: update `pyproject.toml` and `CHANGELOG.md`
2. **Verify**: `python -m pytest -q`
3. **Lint**: `uvx ruff@0.17.1 check --select E4,E7,E9,F src tests scripts`
4. **Build**: `uv build --wheel --no-sources`
5. **Inspect wheel** for static assets and absence of tests/secrets
6. **Tag**: `git tag -a v<version> -m "Release v<version>"`
7. **Publish/deploy only with explicit authorization**

## Post-Release

- [ ] Deploy to staging (authorized host)
- [ ] Smoke test Telegram commands, `/setmode`, Mini App
- [ ] Deploy to production (authorized)
- [ ] Monitor health and logs

## Rollback Criteria

- Provider circuit breakers > 50% open
- Error rate > 5%
- Database migration failure
- Health check failure > 3 consecutive

## Sign-Off

- [ ] Engineering Lead: _______________ Date: ____
- [ ] Security Review: _______________ Date: ____
- [ ] Operations: _______________ Date: ____
