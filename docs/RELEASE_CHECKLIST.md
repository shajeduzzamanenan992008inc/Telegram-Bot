# Release Checklist — Telegram-Bot

## Pre-Release Verification

### Code Quality (verified 2026-11-25)

- [x] Full pytest suite passes — 342 tests on each of Python 3.11, 3.12, 3.13, 3.14, 3.15
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
- [x] Mode-specific regression tests (29) pass
- [x] Dependency audit (`pip-audit`) — no known vulnerabilities
- [x] Branch protection on `main`: strict required checks, 1 reviewer approval,
      force-push and deletion protection (verified via API)
- [x] Dependabot alerts and automated security fixes enabled
- [ ] CodeQL results reviewed (GitHub Actions blocked by account billing)
- [ ] Secret scanning (GitHub: not available for this repository/plan)

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
- [x] `ci.yml` config-validation job validates this repository's YAML/JSON/settings
- [x] `security.yml` CodeQL configuration retained
- [x] `dependency-review.yml` retained
- [ ] Workflows executed successfully on GitHub — **blocked**: every job at
      `446fcae` was refused before its first step with GitHub's failed
      payment/spending-limit annotation

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
