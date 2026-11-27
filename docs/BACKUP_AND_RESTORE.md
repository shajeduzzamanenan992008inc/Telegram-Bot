# Backup and Restore — Telegram-Bot

## Overview
The database supports two backup modes: plain (unencrypted) and encrypted. Both produce consistent SQLite snapshots using the online backup API, preserving WAL mode and all constraints.

## Backup Methods

### 1. Plain Backup (CLI)
```bash
python -m scripts.database_backup /path/to/backup.sqlite3
```
- Fast, no encryption
- Suitable for same-machine restore or trusted environments
- Integrity verified after copy

### 2. Encrypted Backup (CLI)
```bash
python -m scripts.database_backup /path/to/backup.sqlite3.enc --encrypted
```
- Requires `APP_DATA_KEY` configured
- AES-GCM encryption via `cryptography.Fernet`
- Atomic write with temp file + fsync + rename

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

### 2. Verify Backup
```bash
# Plain
sqlite3 backup.sqlite3 "PRAGMA integrity_check"

# Encrypted (verify via restore test)
python -c "
from enan_ultra.crypto import DataCipher
from enan_ultra.config import Settings
s = Settings.load()  # Must have same APP_DATA_KEY
cipher = DataCipher(s.app_data_key)
plaintext = cipher.decrypt(open('backup.sqlite3.enc', 'rb').read())
open('test_restore.sqlite3', 'wb').write(plaintext)
"
sqlite3 test_restore.sqlite3 "PRAGMA integrity_check"
```

### 3. Replace Database
```bash
cp backup.sqlite3 /var/lib/telegram-ai/telegram_ai.sqlite3
# Ensure permissions
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
/opt/enan-ultra/.venv/bin/python -m scripts.database_backup "$DEST" --encrypted
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