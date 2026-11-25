# Backup and Restore — Telegram-Bot

## Overview
The CLI produces encrypted snapshots only. Both the CLI and the programmatic
helpers build a consistent SQLite snapshot with the online backup API, so WAL
content is included and writers are not blocked. `PRAGMA integrity_check` runs
on every snapshot before it is written or restored.

## Backup Methods

### 1. Encrypted Backup (CLI — supported operator path)
```bash
uv run --locked python scripts/database_backup.py backup /path/to/backup.sqlite3.enc
```
- Requires `APP_DATA_KEY` (at least 32 characters), read from the environment or
  `/etc/telegram-ai/bot.env`
- Fernet authenticated encryption (AES-128-CBC with HMAC-SHA256) via `cryptography`
- Atomic write with temp file + fsync + rename; parent directory is created mode `0700`
- Plaintext never persists outside a private temporary directory

### 2. Plain Backup (programmatic only)
The CLI has no unencrypted mode; `database_backup.py restore` also refuses a
plaintext source unless `--allow-unencrypted` is passed explicitly. For a
trusted same-machine copy use `Database.backup_to()` directly.

### 3. Programmatic (Python)
```python
from enan_ultra.storage.database import Database

db = Database(Path("data/telegram_ai.sqlite3"), ...)
await db.open()

# Plain
await db.backup_to(Path("backup.sqlite3"))

# Encrypted (requires APP_DATA_KEY)
await db.backup_encrypted_to(Path("backup.sqlite3.enc"))
```

## Restore Procedure

### 1. Stop the Service
```bash
sudo systemctl stop telegram-ai
```

### 2. Restore to a Non-Live Path and Verify
```bash
# Decrypts into a private temp file, checks SQLite integrity, then copies.
uv run --locked python scripts/database_backup.py restore \
  /path/to/backup.sqlite3.enc --to /tmp/restore-check.sqlite3
sqlite3 /tmp/restore-check.sqlite3 "PRAGMA integrity_check"
```
A wrong `APP_DATA_KEY`, a corrupt file, or a plaintext source (without
`--allow-unencrypted`) exits non-zero without writing the destination.

### 3. Replace the Live Database
```bash
# The script refuses while telegram-ai.service is active.
uv run --locked python scripts/database_backup.py restore \
  /path/to/backup.sqlite3.enc \
  --to /var/lib/telegram-ai/telegram_ai.sqlite3 --replace
```
`--replace` preserves the current database as
`<name>.pre-restore-<UTC-timestamp>` before swapping it in, and removes stale
`-wal`/`-shm` files for the live path. Otherwise copy the verified snapshot
manually and fix ownership:
```bash
chown enan-ultra:enan-ultra /var/lib/telegram-ai/telegram_ai.sqlite3
chmod 600 /var/lib/telegram-ai/telegram_ai.sqlite3
```

### 4. Start Service
```bash
sudo systemctl start telegram-ai
```

### 5. Verify Post-Restore
- Check service health: `systemctl status telegram-ai`
- Verify audit chain on startup (auto-verified in `migrate()`)
- Test a command: `/start` in Telegram

## Automated Daily Backup (Cron)
```bash
# /etc/cron.daily/telegram-ai-backup
#!/bin/bash
set -e
DEST="/var/backups/telegram-ai/$(date +%F).sqlite3.enc"
/opt/enan-ultra/.venv/bin/python /opt/enan-ultra/scripts/database_backup.py backup "$DEST"
# Keep last 30 days
find /var/backups/telegram-ai -name '*.sqlite3.enc' -mtime +30 -delete
```

## Important Notes

### Encryption Key Rotation
- **Do not change `APP_DATA_KEY`** without re-encrypting all backups and the live database
- If key must change: decrypt all backups with old key, re-encrypt with new key
- Live DB: Export all user data, change key, re-import (use `/export` and `/privacy` commands)

### Migration Compatibility
- Backups include schema version
- On restore, `migrate()` will apply any missing migrations
- Downgrades not supported (forward-only migrations)

### Concurrent Access
- Online backup API allows reads during backup
- Writes briefly blocked during checkpoint (milliseconds)
- Schedule during low-traffic periods

### Retention Policy
- Default: 30 days (configurable in cron)
- Minimum recommended: 7 days for point-in-time recovery
- Off-site replication recommended for disaster recovery

## Disaster Recovery Checklist
- [ ] Latest encrypted backup exists and is < 24h old
- [ ] `APP_DATA_KEY` securely stored in password manager
- [ ] Restore procedure tested in staging within last 30 days
- [ ] Off-site backup copy verified (cloud storage / separate server)
- [ ] Recovery time objective (RTO) documented: ~10 minutes
- [ ] Recovery point objective (RPO) documented: ~24 hours