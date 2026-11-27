# Engineering Baseline — Telegram-Bot Repository

## Repository Identity
- **Repository**: https://github.com/shajeduzzamanenan992008inc/Telegram-Bot.git
- **Default Branch**: `main`
- **Current HEAD Commit**: `47067e7` (Merge pull request #14 from shajeduzzamanenan992008inc/feature/admin-zip-update)
- **Repository Name**: `enan-ultra-telegram-ai`
- **Version**: 0.1.0

## Branch Inventory
| Branch | Type | Status |
|--------|------|--------|
| `main` | Primary | Active (HEAD) |
| `codex/admin-zip-update` | Feature | Merged |
| `feature/cross-platform-ci-pilot` | Feature | Open |
| `feature/direct-main-sync` | Feature | Open |
| `feature/release-artifacts` | Feature | Open |
| `feature/update-main-files` | Feature | Open |
| `upgrade/final-quality-gates` | Feature | Open |

## Tags & Releases
- No tags found
- No GitHub Releases found

## Architecture Overview

### Core Package Structure
```
src/enan_ultra/
├── __init__.py              # Package exports
├── __main__.py              # CLI entry point
├── actions.py               # Action registry & execution
├── command_menu.py          # Telegram command scopes
├── config.py                # Validated environment configuration
├── crypto.py                # Encryption utilities
├── dashboard.py             # FastAPI admin dashboard
├── handlers.py              # Telegram command/message handlers
├── mini_app.py              # Telegram Mini App server
├── models.py                # Data models
├── runtime.py               # Application lifecycle
├── security.py              # Security primitives
├── sticker_collector.py     # Sticker catalog
├── files/
│   ├── base.py              # File base classes
│   └── registry.py          # File registry
├── services/
│   ├── agents.py            # Agent orchestration
│   ├── antivirus.py         # ClamAV integration
│   ├── code_sandbox.py      # Docker sandbox
│   ├── conversation.py      # Conversation management
│   ├── conversation_style.py # Reply styling
│   ├── document_export.py   # Export services
│   ├── health.py            # Resource monitoring
│   ├── integrations.py      # Home Assistant, VM control
│   ├── key_rotation.py      # API key rotation
│   ├── media.py             # Media processing
│   ├── mission_control.py   # Mission Control
│   ├── openrouter.py        # OpenRouter provider
│   ├── providers.py         # Provider router
│   ├── sticker_image.py     # Sticker conversion
│   ├── style_profile.py     # User style profiles
│   ├── telegram_deployment.py # Deployment via Telegram
│   ├── telegram_direct_update.py # Direct update service
│   ├── telegram_messages.py # Rich message service
│   ├── telegram_zip_updates.py # ZIP update workflow
│   ├── theme_engine.py      # Adaptive themes
│   ├── voice.py             # Voice synthesis
│   ├── web_reader.py        # Web content fetching
│   └── web_search.py        # Web search
├── storage/
│   └── database.py          # Async SQLite database
└── webapp/
    └── static/              # Mini App assets
```

## Key Components

### 1. Configuration (`config.py`)
- Validated environment loading via `.env` and `dotenv_values()`
- Comprehensive type validation (bool, int, float, csv, provider keys)
- Provider ordering and model allowlists
- 143 configuration parameters

### 2. Database (`storage/database.py`)
- Async SQLite with `aiosqlite`
- WAL mode, foreign keys enabled
- Idempotent versioned migrations (up to v46)
- Encrypted storage with `DataCipher` (AES-GCM)
- Tables: users, conversations, documents, memories, tasks, missions, evolution_proposals, audit_log, usage_events, settings, sticker_catalog, etc.

### 3. Telegram Handlers (`handlers.py`)
- 50+ command handlers
- Conversation management with history bounds
- File/document/media handling
- Admin commands with authorization gates
- Inline query support
- Webhook and polling support

### 4. Provider System (`services/providers.py`, `services/openrouter.py`)
- Multi-provider routing (OpenRouter, Groq, Gemini, Cloudflare, HuggingFace, Ollama)
- Key rotation and circuit breaker
- Tool-capable model allowlists
- Free-tier routing

### 5. Mission Control (`services/mission_control.py`)
- Multi-step missions with persistence
- Encrypted payload storage
- Evolution proposals (self-evolution workflow)

### 6. ZIP Update System (`services/telegram_zip_updates.py`)
- Staged ZIP uploads with quarantine
- Path traversal prevention
- Secret exclusion
- Compression ratio validation
- Dual approval gates (staging + deployment)

### 7. Admin Dashboard (`dashboard.py`)
- FastAPI + Jinja2
- Telegram WebApp authentication
- Session management with CSRF protection

### 8. Mini App (`mini_app.py`, `webapp/`)
- Telegram WebApp integration
- Theme engine with adaptive topics
- User preferences isolation

### 9. Security (`security.py`)
- SSRF protection for external images
- DNS pinning with TLS validation
- Admin/authorization checks
- Safe filename handling

## Current Test Status
- **Total Tests**: 295 (all passing)
- **Test Categories**:
  - Access policy: 2
  - Action results: 5
  - Adaptive theme: 8
  - Antivirus: 5
  - Command menu: 10
  - External image security: 25
  - Final upgrade: 37
  - Group summary modes: 5
  - Media routes: 6
  - Mini app: 3
  - NLU context: 3
  - Omega mission evolution: 18
  - Provider reliability: 23
  - Sticker collector: 12
  - Style profile: 4
  - Telegram deployment: 28
  - Telegram messages: 13
  - Telegram ZIP updates: 28
  - Update idempotency: 5
  - Update sandbox: 1
  - Update upload gate: 7
  - User profile web search: 6
  - Vision handler: 3
  - WebApp theme UI (JS): 3

## Static Analysis
- **Ruff**: All checks pass (E4, E7, E9, F)
- **Python Compile**: No syntax errors
- **JavaScript**: Syntax valid, 3 Node.js tests pass

## Build & Packaging
- **Wheel Build**: Successful (61 files)
- **Web Assets**: Verified in wheel (index.html, app.js, app.css, 11 themes)
- **Dependencies**: 55 packages locked in `uv.lock`

## CI/CD Workflows (`.github/workflows/`)
| Workfile | Triggers | Key Checks |
|----------|----------|------------|
| `ci.yml` | push, PR, dispatch | Multi-Python tests (3.11-3.13), profile checks, packaging |
| `tests.yml` | push, PR | Quality (ruff, compile, JS), audit, build, test matrix (3.11-3.15) |
| `security.yml` | push, PR, weekly | CodeQL Python analysis |
| `config-validation.yml` | push, PR | Config validation |
| `dependency-review.yml` | PR | Dependency review |

## Known Limitations & Technical Debt

### 1. Windows Compatibility
- Unix socket paths for ClamAV (`/var/run/clamav/clamd.ctl`)
- `os.chmod` calls with Unix permissions
- Systemd service files in deploy/
- File permission handling assumes POSIX

### 2. Configuration
- Requires `.env` file with valid tokens (no defaults for production)
- Dashboard and Mini App require HTTPS in production
- Local Bot API server needed for >20MB uploads

### 3. External Dependencies
- ClamAV for malware scanning (optional)
- Docker for code sandbox (optional)
- Telegram Bot API server for large files

### 4. Test Coverage Gaps
- No integration tests with real Telegram API
- No browser/E2E tests for dashboard/Mini App
- No load/performance tests
- No database migration rollback tests

### 5. Documentation
- Many `.md` files but no centralized runbook
- No backup/restore verification procedure
- No production deployment checklist

## Existing Security Posture
- ✅ Parameterized SQL queries
- ✅ AES-GCM encryption for sensitive data
- ✅ SSRF protection for external images
- ✅ Path traversal prevention in ZIP extraction
- ✅ Secret exclusion from ZIP uploads
- ✅ Admin authorization gates
- ✅ Audit logging with hash chain
- ✅ Key rotation with quarantine
- ✅ Rate limiting (user + global)
- ❌ No CSP headers on dashboard
- ❌ No security.txt
- ❌ No automated secret scanning in CI (only dependency-review)

## Deployment Scripts
- `deploy/install_ubuntu.sh` - Ubuntu installation
- `deploy/telegram_ai_deploy.py` - Deployment automation
- `deploy/*.service` - Systemd unit files
- `deploy/code-sandbox.Dockerfile` - Sandbox image
- `scripts/database_backup.py` - Backup utility
- `scripts/init_env.py` - Environment initialization
- `scripts/prepare_deploy_env.py` - Deployment prep

## Immediate Next Steps (Phase 0 Complete)

1. **Phase 1**: Architecture Audit - Review module boundaries, circular deps, error propagation
2. **Phase 2**: Bug Hunt - Deep code inspection for runtime issues
3. **Phase 3**: Telegram Bot Audit - Verify all commands, callbacks, security
4. **Phase 4**: Security Hardening - Comprehensive defensive audit
5. **Phase 5**: Database Integrity - Verify constraints, migrations, rollback
6. **Phase 6**: Frontend/Dashboard/Mini App - HTML/CSS/JS review
7. **Phase 7**: AI Provider Integration - Verify all adapters
8. **Phase 8**: Deployment Portability - Fix Windows/Linux issues
9. **Phase 9**: CI/CD Hardening - Least privilege, secret scanning
10. **Phase 10**: Complete Testing - Add missing test categories
11. **Phase 11**: Documentation - Create required docs
12. **Phase 12**: Final Quality Gates - Verify all criteria