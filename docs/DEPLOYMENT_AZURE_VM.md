# Azure Ubuntu VM deployment

The bot runs as the `enan-ultra` system account under `telegram-ai.service`. The installer prepares Python, locked dependencies, system libraries, Noto Bengali fonts, ClamAV scanning/signature updates, the protected environment file and a hardened systemd unit.

## Install or upgrade

From the project checkout on the VM:

```sh
sudo bash deploy/install_ubuntu.sh
sudoedit /etc/telegram-ai/bot.env
sudo bash deploy/install_ubuntu.sh
sudo systemctl status telegram-ai
```

The installer preserves existing environment values and adds new keys from `.env.example`. It installs dependencies from `uv.lock`; the Rich Message compatibility layer does not require replacing PTB 22.8. `fonts-noto-core` is installed for Bengali PDF exports.

ClamAV, its daemon and the `clamav-freshclam` signature updater are installed and enabled. A newly created bot environment enables `MALWARE_SCAN_ENABLED=true`; on an existing deployment the installer preserves an explicit scanner setting, and adds the setting enabled if it was absent. The service receives membership in the `clamav` group to access `/var/run/clamav/clamd.ctl`. Before processing an upload, the bot streams its in-memory bytes to ClamAV. A detection blocks the file and sends a rate-limited alert to the configured admin chat; if ClamAV is unavailable or returns an inconclusive error, the upload fails closed and is not sent to an AI provider.

Verify the scanner and socket after installation:

```sh
sudo systemctl status clamav-freshclam clamav-daemon --no-pager
sudo -u enan-ultra test -S /var/run/clamav/clamd.ctl
grep -E '^(MALWARE_SCAN_ENABLED|CLAMAV_SOCKET_PATH)=' /etc/telegram-ai/bot.env
```

Fresh signatures can take a while to download after first install. Let `clamav-freshclam` finish and confirm `clamav-daemon` is active before sending uploads. Check VM memory capacity: ClamAV's signature database uses significant RAM. Do not turn off scanning to mask a daemon failure; check `journalctl -u clamav-daemon -u clamav-freshclam` instead. ClamAV is signature-based malware scanning, not a full endpoint-detection system. SSH brute-force protection and notifications are separate; configure and verify Fail2Ban independently if required.

Use Python 3.11 by default or set `PYTHON_VERSION` to a supported version (3.11–3.15). Keep `/etc/telegram-ai/bot.env` root-owned and mode `0600`. Set numeric admin IDs and at least one supported provider before starting the service.

## Verify and monitor

```sh
sudo systemctl status telegram-ai
sudo journalctl -u telegram-ai -f
```

One command checks the running deployment — service state, health marker,
database, Telegram `getMe`, ClamAV socket and Mini App `/healthz` — without
sending a message or restarting anything:

```sh
sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py
```

Expected output, the manual Telegram checks and a failure-to-fix map live in
[live deployment verification](LIVE_DEPLOY_VERIFICATION.md).

Run `/status` and `/providers` from an authorized admin account. Check that `/start` shows its inline menu, `/cancel` is listed, a private chat gets a live preview when the Bot API supports Rich Messages, and `/export pdf` produces a Bengali-readable PDF. Send a normal supported attachment to verify a clean scan completes. Do not use a production token for automated tests.

## Optional Telegram Mini App

The adaptive theme workspace is disabled by default. Follow [Mini App deployment](MINI_APP.md) to configure an HTTPS domain and reverse proxy, then set `MINI_APP_URL=https://your-domain.example` in `/etc/telegram-ai/bot.env` and restart the service. The app server listens only on `127.0.0.1:8788`; do not expose that port directly. On startup, the bot configures the Telegram chat menu button and `/app` command. Verify `/app`, theme controls, and a short chat from an authorized Telegram account.

## Rollback

Before an upgrade, retain the previous application checkout and the protected environment file. Restore the previous checkout and dependency lock, then run the installer again. For database rollback, restore a verified encrypted snapshot using the same `APP_DATA_KEY`; follow the backup instructions in the README and stop the systemd service during restore.

This workspace change has not been deployed to Azure. A Telegram account, provider credentials, compatible Bot API server and VM are needed for the live smoke check.
