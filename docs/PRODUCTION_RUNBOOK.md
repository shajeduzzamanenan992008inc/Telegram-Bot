# Production Runbook — Telegram-Bot

## Service Management

### Start Service (Linux)
```bash
sudo systemctl start telegram-ai
sudo systemctl enable telegram-ai
```

### Check Status
```bash
sudo systemctl status telegram-ai
journalctl -u telegram-ai -f
```

### One-command live verification
```bash
sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py
```

Read-only: environment file, systemd state, health marker, database, Telegram
`getMe`, ClamAV socket and Mini App `/healthz`. Exit code 0 means every applicable
check passed. Full runbook: [LIVE_DEPLOY_VERIFICATION.md](LIVE_DEPLOY_VERIFICATION.md).

### Restart Gracefully
```bash
sudo systemctl reload telegram-ai  # If supported
# Or via Telegram: /vm then approve restart
```

### View Logs
```bash
journalctl -u telegram-ai --since "1 hour ago"
# Or via Dashboard: /api/v1/vm (requires admin auth)
```

## Configuration

### Environment File
Location: `/etc/telegram-ai/bot.env` (per systemd `EnvironmentFile`)

### Required Variables
| Variable | Description | Example |
| :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | BotFather token | `123456:ABC...` |
| `ADMIN_CHAT_ID` | Admin group/user chat ID | `-1001234567890` |
| `ADMIN_USER_IDS` | Comma-separated admin user IDs | `123456789` |
| `APP_DATA_KEY` | 32+ char encryption key | `openssl rand -hex 32` |
| `DASHBOARD_AUTH_TOKEN` | 32+ char dashboard key | `openssl rand -hex 32` |

### AI Provider (at least one)
```bash
OPENROUTER_API_KEYS=key1,key2
OPENROUTER_MODEL=openrouter/auto
```

## Database Operations

### Backup (Manual)
```bash
python -m scripts.database_backup /path/to/backup.sqlite3
```

### Encrypted Backup
```bash
python -m scripts.database_backup /path/to/backup.sqlite3.enc --encrypted
```

### Restore
```bash
# Stop service first
sudo systemctl stop telegram-ai
# Copy backup to database location
cp backup.sqlite3 /var/lib/telegram-ai/telegram_ai.sqlite3
sudo systemctl start telegram-ai
```

## Emergency Procedures

### Provider Failure Alert
- Check dashboard `/api/v1/providers` for circuit status
- Wait for auto-recovery (cooldown: 60s default)
- Manual recheck: Dashboard "Recheck" button or `/api/v1/recheck/<provider>`

### Stuck Deployment
1. Check `/api/v1/vm` logs
2. Verify staging directory: `/opt/enan-ultra/staging`
3. Cancel via Telegram: `/update cancel` (admin only)
4. Force rollback: Restart service with previous release

### Database Corruption
1. Stop service
2. Verify integrity: `sqlite3 telegram_ai.sqlite3 "PRAGMA integrity_check"`
3. Restore from latest encrypted backup
4. Verify audit chain on startup

## Monitoring Checklist (Daily)
- [ ] Service running (`systemctl is-active telegram-ai`)
- [ ] Disk space > 20% (`df -h /var/lib/telegram-ai`)
- [ ] Memory usage normal (`free -h`)
- [ ] No provider circuits open (Dashboard: Providers)
- [ ] Error rate < 1% (Journal: `journalctl -u telegram-ai -p err --since "24h" | wc -l`)

## Contacts
- Primary Admin: Telegram ID in `ADMIN_USER_IDS`
- Infra: [Your Infrastructure Team]
- Security: [Your Security Team]