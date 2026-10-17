# Telegram ZIP updates: staging and controlled deployment

This feature provides administrator-only ZIP upload, isolated validation/staging, a distinct staging decision, and (when an operator explicitly enables it) a separate production approval sent to a root-owned, narrowly scoped deployment broker. `UPDATE_DEPLOYMENT_ENABLED` defaults to `false`. The operator must also install the broker units, verify the exact systemd layout, and create the root-owned enable flag. The Telegram service is never granted sudo or direct systemd access.

## Workflow

1. A user in `ADMIN_USER_IDS` runs `/update` in the configured `ADMIN_CHAT_ID`.
2. The bot opens a five-minute, single-upload session for that administrator and chat. `/update_cancel` closes it.
3. The administrator sends a ZIP document in the same chat. Other documents and ZIPs sent without an active session are not treated as update requests.
4. The bot applies the configured upload/archive limits, validates every entry, and extracts only allowlisted paths to a private staging directory beside `DATABASE_PATH`.
5. It checks Python syntax without importing or executing candidate files. If `CODE_SANDBOX_ENABLED=true` and the configured rootless Docker sandbox is available, it also runs `pytest` offline in that disposable sandbox. Otherwise the test result is `BLOCKED` / `NOT RUN`; it is never reported as a pass.
6. The administrator receives the changed-file list, test result, dependency changes, warnings and manifest hash, and can request the full manifest, approve staging, reject or cancel the release. Staging approval is available only if syntax and sandbox tests pass.
7. Staging approval is **not** production approval. Once approved, the bot checks deployment configuration and contacts the local broker for a read-only preflight. Only a ready release gets a separate **Approve production deployment** button.
8. Production approval is separately authorized and bound to the same exact manifest. The broker revalidates approval evidence, archive/hash/file list, current-live base hashes, required test state and its root-owned enable flag, snapshots the current application files, applies only permitted files, restarts `telegram-ai.service`, and requires a fresh runtime health marker. If activation/restart/health fails after file changes, it restores the verified code snapshot and checks health again.
9. Staged data expires after 30 minutes. The broker retains at most five verified snapshots and stops rather than deleting old recovery points when its storage budget is reached. Duplicate/replayed deployment IDs are rejected. Broker results are delivered to the configured admin chat after the bot starts again.

Database migrations and dependency installation are never run by this workflow. Releases changing `pyproject.toml`, `requirements.txt`, or `uv.lock` are blocked from Telegram deployment and require a separately reviewed operator procedure. The broker also refuses to change its own approval/deployment policy files, configuration, security code, handler or runtime entry points. Its flat-checkout procedure is not compatible with a symlink-based release layout.

The project systemd unit now puts `/opt/enan-ultra/src` on `PYTHONPATH`; without this, editing source files would not necessarily change the installed wheel used by the existing virtual environment. This is a service configuration change and needs an operator install/restart before enabling the broker.

### Operator setup and enablement

Install/upgrade from a trusted repository checkout using the documented Ubuntu installer. It installs the root-owned helper and socket/service units, makes the application checkout root-owned and removes group/world write permissions, creates a separate root-owned backup/audit area, and enables the socket. The deployer remains disabled until both gates are explicitly set:

1. Set `UPDATE_DEPLOYMENT_ENABLED=true` in `/etc/telegram-ai/bot.env`.
2. After confirming the service is actually `telegram-ai.service`, runs as `enan-ultra`, uses `WorkingDirectory=/opt/enan-ultra`, and launches the matching `.venv/bin/python -m enan_ultra`, create `/etc/telegram-ai/zip-deploy.enabled` as a root-owned, non-group/world-writable regular file.
3. Restart through the documented operator procedure and confirm `/run/telegram-ai-deploy.sock` is a root:`enan-ultra` socket. The broker refuses any other service identity, working directory, entry point, ownership, health-marker identity, absent disk space, or stale/bad health signal.

This repository's Azure host was previously observed running a different checkout, service user and entry point. On that host, broker preflight must remain blocked until an operator separately reconciles the deployment layout. Do not change that production host as part of development or assume local Windows access authorizes a remote deployment.

## Archive limits and supported contents

The compressed file limit is `MAX_UPLOAD_BYTES` (20 MiB by default, matching the standard Telegram Bot API download limit). Expanded size uses `MAX_ARCHIVE_EXPANDED_BYTES` (50 MiB by default); update ZIP entry count uses `UPDATE_MAX_FILES` (250 by default, counting files and explicit directory entries). ZIP depth, path length, and per-entry compression ratio use `UPDATE_MAX_DEPTH` (8), `UPDATE_MAX_PATH_LENGTH` (240), and `UPDATE_MAX_COMPRESSION_RATIO` (100).

Archives must be repository-root ZIPs containing `src/enan_ultra/__init__.py` and `src/enan_ultra/runtime.py`. Accepted paths are:

- `src/enan_ultra/**`
- `tests/**`
- `docs/**`
- `README.md`, `CHANGELOG.md`, `.env.example`, `pyproject.toml`, `requirements.txt`, and `uv.lock`

Under `src/enan_ultra/`, only `.py`, `.json`, `.html`, `.css`, and `.js` files are accepted; `tests/` accepts those text/test suffixes plus `.txt` and `.md`, while `docs/` accepts Markdown only. Bytecode and cache directories are rejected.

Only regular files/directories and stored/deflate compression are accepted. Absolute paths, traversal, Windows path separators and reserved names, symlinks/special files, encrypted entries, corrupt content, duplicate/case-colliding paths, excessive expansion, and protected configuration/data files are rejected. All paths and member data are validated before the first staged file is written.

The archive is compared with allowlisted files in the current working tree. The manifest reports added, changed, unchanged and removed files with sizes, SHA-256 hashes, previous live hashes and explicit syntax/test results. Dependency metadata changes are reported but never installed. Production deployment requires the exact reviewed manifest and archive hashes, plus current-live hashes still matching the manifest's prior hashes.

## Staging and test requirements

Staging is stored in `.telegram-zip-updates` beside `DATABASE_PATH`, with private directory/file permissions on POSIX systems. It does not copy over `.env`, the database, backups, uploads or runtime state. The broker snapshots only regular application package source files under `src/enan_ultra/`; other state is never copied or altered. It never executes ZIP content, installs dependencies, or runs database migrations.

To request isolated tests, configure the existing rootless Docker sandbox (`CODE_SANDBOX_ENABLED=true`, a locally available `CODE_SANDBOX_IMAGE`, and the documented sandbox limits). The test container has no network, is read-only except its disposable work/tmp files, and receives only the submitted archive. It does not install dependencies from the archive. Missing Docker, an unavailable image, disabled sandboxing, timeout, or missing test dependencies is reported as `BLOCKED` or `FAIL`, never success.

The workflow audit trail records upload start, validation/rejection, staging, test result, decisions, and broker outcomes in the existing database audit events. The broker independently keeps a root-owned deployment journal/audit and verified recovery snapshots under `/var/lib/telegram-ai-deploy`. Upload and approval state remains process-local; a bot restart invalidates unsubmitted approvals. A submitted deployment ID is one-shot in the broker journal.

Health requires systemd `ActiveState=active`, a changed main PID, a fresh atomic marker for that same PID, an open application database that passed `SELECT 1`, and a Telegram `getMe` identity matching the pre-deploy bot. This confirms process/database initialization and Telegram credential acceptance without sending a customer message or making an AI provider request. It does not prove every command, provider, or external integration works.

## Telegram report

The report includes release ID, archive name/size, changed files, actual syntax/sandbox outcomes, dependency changes, warnings, manifest hash, and backup/rollback preflight status. Staging and production actions are separate. A production request is not reported successful until the broker restarts the service and observes application health. A verified rollback or rollback failure is separately recorded and reported after the bot returns. If both the new release and recovery service fail to start, Telegram reporting is unavailable; use the root-owned broker journal and systemd journal for recovery.
