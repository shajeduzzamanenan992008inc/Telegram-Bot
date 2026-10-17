# Telegram ZIP update troubleshooting

## `/update` is unavailable or rejected

- Check that the sender is listed in `ADMIN_USER_IDS`.
- Run the command in the exact `ADMIN_CHAT_ID`; administrators cannot start or approve a ZIP workflow in another chat.
- Check that the account is not blocked in the bot database.
- The upload session expires after five minutes. Run `/update` again if it expired.

## ZIP upload is rejected

- Send one Telegram **document** whose filename ends in `.zip`; photos or other documents are not update packages.
- Standard Telegram downloads are limited to 20 MiB. `MAX_UPLOAD_BYTES` cannot exceed that on the standard Bot API.
- Check `MAX_ARCHIVE_EXPANDED_BYTES`, `UPDATE_MAX_FILES`, `UPDATE_MAX_DEPTH`, `UPDATE_MAX_PATH_LENGTH`, and `UPDATE_MAX_COMPRESSION_RATIO`.
- Use a repository-root archive, not a ZIP with an extra enclosing directory. It must include `src/enan_ultra/__init__.py` and `src/enan_ultra/runtime.py`.
- Remove `.env`, credentials, database files/backups, uploads, `.git`, virtual environments, service/deployment files, and any path outside the documented allowlist.
- Symlinks, encrypted ZIPs, unsupported compression, duplicate/case-colliding paths, corrupt CRCs, and non-portable path names are not supported.

## Tests show `BLOCKED` or `FAIL`

`BLOCKED` means isolated tests did not run, for example because `CODE_SANDBOX_ENABLED` is false, rootless Docker is unavailable, or the configured image is missing. `FAIL` means the test process returned a non-zero status or timed out. Neither outcome can be approved for staging. Inspect the sandbox configuration and logs; do not relax network isolation or install archive dependencies automatically.

The test image must already contain dependencies needed by the submitted test suite. This workflow intentionally does not install packages from the candidate ZIP. Python syntax checks are independent of the sandbox and do not execute candidate code.

## Approval callback is rejected

Upload/review state expires after 30 minutes and exists only in process memory. Restarting the bot invalidates outstanding callbacks. Start a fresh `/update` session and upload the archive again. A changed release file, manifest, or uploaded archive invalidates approval. Approve only after reviewing the exact SHA-256 shown in the preview.

## Staging disk space or permissions

Staging lives in `.telegram-zip-updates` beside `DATABASE_PATH`; the service account needs write access there. Do not grant the bot additional `sudo` or systemd rights to resolve a staging issue. Check free space and ownership, then review any orphaned directory before removing it.

## Production approval is missing or preflight is blocked

Staging approval is not production approval. Production requires `UPDATE_DEPLOYMENT_ENABLED=true`, the installed root-owned deployment socket/service, and `/etc/telegram-ai/zip-deploy.enabled`. The broker checks the exact running service account, working directory, Python entry point, root-owned checkout, fresh runtime health marker, available disk, and snapshot quota. Any mismatch is a hard block; do not add sudo to the bot to work around it.

The operator selected `enan-ultra.service` as the intended application. The VM unit runs from `/home/azureenan/Telegram-Bot-main`, but the interpreter imports the installed package from `.venv/lib/python3.12/site-packages/enan_ultra`; the checkout has no Git metadata and its fingerprints differ from the local workspace. The service account `azureenan` belongs to `sudo` and owns the writable code tree; `.env` is mode `0664`. This is not an acceptable unprivileged deployment layout. The broker is intentionally configured for a separate root-owned `/opt/enan-ultra` checkout and service identity. Keep preflight blocked until an operator identifies the authoritative source revision and approves a protected release layout with a dedicated non-sudo runtime account; never weaken path or ownership checks to bypass this mismatch. See [deployment and rollback](./DEPLOYMENT_AND_ROLLBACK.md#azure-target-reconciliation-hold-2026-10-17).

The separate `telegram-ai.service` Telethon application is not the selected package deployment target. Its journal showed repeated interactive authentication `EOFError`s with systemd stdin set to `/dev/null`. Do not apply the `enan_ultra` ZIP workflow to that application.

## Deployment failed or rolled back

The admin report distinguishes `failed`, `rolled_back`, and `rollback_failed`. A failure before activation leaves live files unchanged. A failure after activation restores the verified source snapshot, restarts the fixed service, and checks the previous release. Inspect the redacted failure code, then use the root-owned journal under `/var/lib/telegram-ai-deploy/deployment-history/` and systemd journal during an approved recovery.

If the service restarts while an update is `queued`, `validating`, or `snapshot_created`, the broker records it failed before activation. If interrupted during `applying` or `restarting`, the broker restores the recorded snapshot on its next start. If `rollback_failed` is reported, stop further updates and use the recorded verified snapshot for operator recovery. Do not restore an unverified path or database backup.

## Snapshot quota or invalid release

Only five source snapshots or 500 MiB are retained. They are not automatically pruned; a full quota blocks deployment until an operator reviews retention and preserves an off-host verified recovery copy. Releases changing dependencies or the broker/approval/runtime/security files are intentionally refused. Use a separately reviewed operator release for those changes.

If the bot restarts after requesting deployment, approval state may disappear, but the root-owned broker journal remains authoritative. The broker's release ID is one-shot; do not retry a terminal deployment ID. The bot reports unnotified results after it successfully starts again. If it never returns, inspect the service/broker journals from the authorized operator account.
