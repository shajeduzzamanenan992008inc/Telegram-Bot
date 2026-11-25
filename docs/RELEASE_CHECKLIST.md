# Release Checklist — Telegram-Bot

## Pre-Release Verification

### Code Quality (verified 2026-11-25)

- [x] Full pytest suite passes — 322 tests
- [x] Ruff (`E4,E7,E9,F`) clean across `src`, `tests`, `scripts`
- [x] Python compile check passes
- [x] JavaScript syntax check passes
- [x] JavaScript tests pass (3)
- [x] Wheel builds (`enan_ultra_telegram_ai-0.1.0-py3-none-any.whl`, 62 files)
- [x] Mini App assets verified in the wheel (static files + 10 themes)
- [x] Configuration validation (`scripts/validate_config.py`) passes

### Security

- [x] Private/Public bypass fixed: database mode precedes ordinary-user checks
- [x] Fail-closed behavior when the persisted mode cannot be read
- [x] Global mode row seeded/upserted correctly (migration 17)
- [x] `/setmode` command collision removed
- [x] Mode-specific regression tests (27) pass
- [ ] CodeQL results reviewed (runs on GitHub; not run here)
- [ ] Dependency audit (`pip-audit`) run against the lock (CI job; not run here)
- [ ] Branch protection and secret scanning verified (GitHub settings)

### Documentation

- [x] `docs/ACCESS_CONTROL_MODES.md`
- [x] `docs/PRIVATE_PUBLIC_MODE_SECURITY.md`
- [x] `docs/PRIVATE_PUBLIC_MODE_TEST_REPORT.md`
- [x] `docs/DEPLOYMENT_READINESS_REVIEW.md`
- [x] `docs/KNOWN_LIMITATIONS.md`
- [x] `docs/FINAL_ENGINEERING_REPORT.md`
- [x] `docs/RELEASE_CHECKLIST.md`

### Configuration

- [x] `.env.example` has 138 unique keys and matches literal loader keys
- [x] Required variables documented
- [x] Invalid `ACCESS_MODE` / empty allowlist rejected by the loader

### CI/CD

- [x] `ci.yml` targets this repository (Python 3.11–3.15 matrix, pytest, ruff,
      compile, JS, dependency audit, wheel, Mini App assets, config validation)
- [x] `config-validation.yml` validates this repository's YAML/JSON/settings
- [x] `security.yml` CodeQL configuration retained
- [x] `dependency-review.yml` retained
- [ ] Workflows executed successfully on GitHub (not run from this workspace)

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
