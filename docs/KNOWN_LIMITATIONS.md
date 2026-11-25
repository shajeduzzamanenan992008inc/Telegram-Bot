# Known Limitations — Telegram-Bot

## Platform Support

### Windows
- **Service Management**: No native Windows service installer. Requires NSSM or manual startup.
- **Named Pipes**: Deployment socket and ClamAV paths use `\\.\pipe\...` defaults but untested in production.
- **File Permissions**: POSIX `chmod` calls skipped on Windows; relies on OS ACLs.
- **Docker Desktop Required**: Code sandbox requires Docker Desktop with WSL2 backend.

### macOS
- Not tested. Should work with minor adjustments (similar to Linux POSIX paths).

## Testing Coverage Gaps

| Area | Current Status | Gap |
| :--- | :--- | :--- |
| Browser/E2E Tests | ❌ None | Dashboard & Mini App not tested in real browser |
| Load/Performance | ❌ None | No concurrent user simulation |
| Database Migration Rollback | ❌ Not tested | Forward-only migrations; no downgrade path tested |
| Network Partition | ❌ None | No chaos engineering tests |
| Cross-Version Upgrade | ❌ Manual | No automated upgrade testing between releases |

## Feature Limitations

### AI Provider
- **Free Tier Only**: `AI_FREE_ONLY` restricts to pre-approved models
- **No Local LLM**: Ollama supported but untested in CI
- **Web Search**: Requires OpenRouter; no direct provider support
- **Image Generation**: OpenRouter only; no local diffusion models

### Telegram Integration
- **Webhook Only**: No webhook mode implemented (polling only)
- **No MTProto**: Uses Bot API only; no raw MTProto support
- **Group Chat Limits**: No federation/multi-group coordination

### File Handling
- **Max Upload**: 20 MB without local Bot API server
- **No Streaming Upload**: Large files loaded fully into memory
- **Archive Depth**: Hardcoded limits (8 levels, 1000 files)

### Self-Evolution
- **Sandbox Required**: Docker mandatory for `/test`, `/evolve_test`, `/evolve_stage`
- **Rootless Docker**: Must run rootless for security
- **No Auto-Deploy**: Human approval required at every gate

### Admin Dashboard
- **Loopback Only**: Binding to `127.0.0.1` by design (no external access)
- **No Multi-Admin Sessions**: Single admin session per browser
- **No Real-Time Logs**: Sanitized logs only (20 lines)

### Mini App
- **HTTPS Required**: Must use configured HTTPS URL (no localhost HTTP in production)
- **Telegram WebApp Only**: Cannot be opened directly in browser
- **Theme Registry**: Static JSON themes; no dynamic theme creation

## Operational Limitations

### Monitoring
- **No Prometheus Metrics**: Resource metrics via HTTP only
- **No Distributed Tracing**: Single-instance architecture
- **Alerting**: Admin Telegram alerts only; no PagerDuty/Slack/webhook integration

### Scaling
- **Single Process**: No horizontal scaling (async but single event loop)
- **No Sharding**: Single SQLite database
- **Connection Pool**: Limited by `aiosqlite` (no connection pooling)

### Data Retention
- **Fixed Retention**: `DOCUMENT_RETENTION_HOURS` applies globally
- **No Tiered Storage**: All data in single SQLite file
- **Audit Chain**: Immutable but not externally verifiable (no Merkle proofs)

## Dependencies

### Pinned Versions
- `python-telegram-bot==22.8` (not latest)
- `opencv-python-headless==5.0.0.93` (large binary wheel)
- `reportlab[shaping]==5.0.1` (complex dependency tree)

### System Dependencies
- **ClamAV**: Optional but requires daemon (`clamav-daemon.service`)
- **Docker**: Required for sandbox (rootless)
- **Fonts**: `fonts-noto-core` for Bengali PDF rendering

## Technical Debt

### Code Organization
- `handlers.py` ~4900 lines (consider splitting by domain)
- `runtime.py` imports 17 modules (tight coupling)
- `providers.py` ~1272 lines (complex routing logic)

### Async Patterns
- Mixed `asyncio.Lock` and `threading.RLock` (ZIP updates)
- Some sync I/O in async context (subprocess, file ops)

### Type Safety
- Some `Any` types in service interfaces
- Protocol classes but no strict runtime validation

## Acceptance
These limitations are **documented and accepted** for the current release scope. They do not block production deployment on Linux with standard operational procedures.

## Future Roadmap
1. Webhook support + webhook security
2. Multi-instance coordination (Redis pub/sub)
3. Prometheus metrics + Grafana dashboards
4. Automated upgrade testing
5. Windows service installer
6. Browser test automation (Playwright)