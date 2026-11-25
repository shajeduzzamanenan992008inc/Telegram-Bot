# Release Checklist — Telegram-Bot

## Pre-Release Verification

### Code Quality
- [x] All tests pass (295/295)
- [x] Ruff linting clean (E4,E7,E9,F)
- [x] Python compile check passes
- [x] JavaScript syntax check passes
- [x] JavaScript tests pass (3/3)
- [x] Wheel builds successfully (61 files)
- [x] Web assets verified in wheel

### Security
- [x] No hardcoded secrets
- [x] SQL injection prevented (parameterized queries)
- [x] SSRF protection implemented
- [x] Path traversal prevention in ZIP handling
- [x] CSP headers present (review recommended)
- [x] Admin authentication via HMAC
- [x] Secrets loaded from `.env` only

### Documentation
- [x] ENGINEERING_BASELINE.md
- [x] FULL_REPOSITORY_AUDIT.md
- [x] BUG_FIX_REPORT.md
- [x] SECURITY_AUDIT.md
- [x] SECURITY_HARDENING.md
- [x] TESTING_REPORT.md
- [x] DEPLOYMENT_READINESS_REVIEW.md
- [x] PRODUCTION_RUNBOOK.md
- [x] BACKUP_AND_RESTORE.md
- [x] RELEASE_CHECKLIST.md
- [x] KNOWN_LIMITATIONS.md
- [x] FINAL_ENGINEERING_REPORT.md

### Configuration
- [x] `.env.example` complete (143 settings)
- [x] All required variables documented
- [x] Default values sensible
- [x] Validation errors clear

### CI/CD
- [x] Multi-Python test matrix (3.11-3.15)
- [x] Dependency audit (pip-audit)
- [x] CodeQL security scanning
- [x] Config validation workflow
- [x] Dependency review on PRs

## Release Process

1. **Version Bump**: Update `pyproject.toml` version
2. **Changelog**: Update `CHANGELOG.md`
3. **Tag**: `git tag -a v<version> -m "Release v<version>"`
4. **Build**: `uv build --wheel --no-sources`
5. **Verify**: `python verify_wheel.py`
6. **Publish**: `uv publish` (if configured)

## Post-Release
- [ ] Deploy to staging
- [ ] Smoke test staging
- [ ] Deploy to production
- [ ] Monitor for 1 hour
- [ ] Update runbook if needed

## Rollback Criteria
- Provider circuit breakers > 50% open
- Error rate > 5%
- Database migration failure
- Health check failure > 3 consecutive

## Sign-Off
- [ ] Engineering Lead: _______________ Date: ____
- [ ] Security Review: _______________ Date: ____
- [ ] Operations: _______________ Date: ____