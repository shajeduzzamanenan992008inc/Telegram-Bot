# Live deployment verification

This runbook turns the two open items — "live Telegram Bot smoke test" and
"Linux systemd deployment" — into one command you can run as soon as a real host
and a real bot token exist. `scripts/smoke_deployment.py` is read-only: it never
sends a message, never calls an AI provider and never restarts the service.

## What it proves

| Check | Passing means |
| --- | --- |
| environment file | `/etc/telegram-ai/bot.env` is readable, mode `0600`, owned by root |
| required keys | `TELEGRAM_BOT_TOKEN`, `ADMIN_USER_IDS`, `APP_DATA_KEY`, `DASHBOARD_AUTH_TOKEN` are set (values are never printed) |
| database path | absolute and inside `/var/lib/telegram-ai`, matching the systemd write boundary |
| systemd service | `telegram-ai.service` is `active` with a real `MainPID` |
| health marker | `/var/lib/telegram-ai/runtime-health.json` is fresh, bound to that PID, reports the database open and points at the release tree |
| database | a read-only `SELECT 1` and `PRAGMA quick_check` succeed |
| state disk | at least 512 MiB free where the database lives |
| telegram getMe | the token is live and the bot identity equals the health-marker identity |
| clamav socket | when `MALWARE_SCAN_ENABLED=true`, clamd answers `PONG` |
| mini app healthz | when `MINI_APP_URL` is set, loopback `/healthz` returns `{"status":"ok"}` through the proxy headers |

Missing optional pieces (Mini App URL, ClamAV, deployment marker) report `SKIP`
with the reason; a `SKIP` is never counted as a pass for that area.

## 1. Prepare the host (once)

```sh
git clone https://github.com/shajeduzzamanenan992008inc/Telegram-Bot.git
cd Telegram-Bot
sudo bash deploy/install_ubuntu.sh        # exits 2 on first run, before credentials exist
sudoedit /etc/telegram-ai/bot.env         # token, admin ids, APP_DATA_KEY, provider key
sudo bash deploy/install_ubuntu.sh
```

Enable the deployment health marker so the gate is verifiable:

```sh
sudo sed -i 's/^UPDATE_DEPLOYMENT_ENABLED=.*/UPDATE_DEPLOYMENT_ENABLED=true/' /etc/telegram-ai/bot.env
grep -q '^UPDATE_DEPLOYMENT_ENABLED=' /etc/telegram-ai/bot.env || echo 'UPDATE_DEPLOYMENT_ENABLED=true' | sudo tee -a /etc/telegram-ai/bot.env
sudo systemctl restart telegram-ai
```

## 2. Verify in one command

```sh
sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py
```

Exit code `0` = everything applicable passed. `1` = at least one failure (the
table names it). `2` = bad arguments.

Useful flags:

```sh
# Machine-readable, for a deploy pipeline
sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py --json

# Offline host check (no Telegram call, e.g. a locked-down network)
sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py --skip-telegram

# Only the service and database
sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py --skip-telegram --skip-clamav --skip-mini-app
```

Run it against a copy of the checkout before deploying if you like
(`--env-file ./bot.env --service telegram-ai`); on a non-systemd machine the
service, ClamAV and marker checks fail with an explicit reason instead of
crashing.

## 3. Read the output

```text
CHECK                 STATUS  DETAIL
environment file      PASS    /etc/telegram-ai/bot.env parsed (146 keys)
env permissions       PASS    mode 0o600, owner uid 0
required keys         PASS    4 required keys present (values hidden)
database path         PASS    /var/lib/telegram-ai/telegram_ai.sqlite3
systemd service       PASS    telegram-ai active (PID 4127, restarts 0)
health marker         PASS    PID 4127, bot id 7123456789, written 0.4 min ago
database              PASS    read-only query ok, 12 tables
state disk            PASS    87% free on /var/lib/telegram-ai
telegram getMe        PASS    @your_bot (id 7123456789)
clamav socket         PASS    /var/run/clamav/clamd.ctl answered PONG
mini app healthz      SKIP    MINI_APP_URL is not configured
```

## 4. Manual Telegram checks (needs a human with an admin account)

The script cannot prove conversation behaviour without sending real messages.
From an account listed in `ADMIN_USER_IDS`, in a private chat with the bot:

1. `/status` — reports the running version and health.
2. `/providers` — shows provider circuit state; none should be open.
3. `/start` — inline menu appears and `/cancel` is listed.
4. `/export pdf` — PDF opens with readable Bengali text (the Latin/Bengali font split).
5. Send one small supported attachment — upload succeeds after the ClamAV scan.
6. If `MINI_APP_URL` is set: `/app` opens over HTTPS, the theme switches, and one short chat reply arrives.

Also confirm the journal is quiet during those steps:

```sh
sudo journalctl -u telegram-ai --since "10 min ago" -p warning
```

## 5. Troubleshooting map

| FAIL line | First thing to check |
| --- | --- |
| `environment file` / `env permissions` | `sudo ls -l /etc/telegram-ai/bot.env` — must be `-rw------- root root`; re-run the installer if not |
| `required keys` | `sudoedit /etc/telegram-ai/bot.env`, set the missing key, `sudo systemctl restart telegram-ai` |
| `database path` | `grep ^DATABASE_PATH /etc/telegram-ai/bot.env`; it must stay under `/var/lib/telegram-ai` |
| `systemd service` | `sudo systemctl status telegram-ai`, `sudo journalctl -u telegram-ai -n 100 --no-pager` |
| `health marker` | marker needs `UPDATE_DEPLOYMENT_ENABLED=true` and a clean startup; check the journal for the startup line "Bot startup complete" |
| `database` | `sudo sqlite3 /var/lib/telegram-ai/telegram_ai.sqlite3 'PRAGMA quick_check;'`; restore a verified backup if it is not `ok` |
| `state disk` | free space or move state to a larger volume before restarting |
| `telegram getMe` | token revoked or wrong bot; check `HTTP 401`, then rotate the token in BotFather and update the env file |
| `clamav socket` | `sudo systemctl status clamav-daemon clamav-freshclam`, `sudo -u enan-ultra test -S /var/run/clamav/clamd.ctl` |
| `mini app healthz` | `421` means the proxy Host/`X-Forwarded-Proto` headers are wrong; `403` means an Origin mismatch; verify the reverse proxy config in `docs/MINI_APP.md` |

## 6. Record the result

Paste the script output (it contains no secrets) into the release ticket, then
tick the matching rows in `docs/RELEASE_CHECKLIST.md` and
`docs/DEPLOYMENT_READINESS_REVIEW.md`. Only mark the smoke test done when the
exit code was `0` *and* step 4 passed for the bot account you actually ship.
