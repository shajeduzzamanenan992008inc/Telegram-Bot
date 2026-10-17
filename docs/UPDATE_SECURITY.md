# Telegram ZIP update security

The `/update` feature is a direct code overlay initiated by the bot's configured update administrator. ZIP contents are executable project code after the Python process restarts, so only use packages from a trusted release source.

## Authorization and handling

- The command and document handler require `update.effective_user.id == settings.admin_id`; `settings.admin_id` is loaded from `ADMIN_ID` in `.env`. For backward compatibility, an unset `ADMIN_ID` resolves to the lowest ID in the required `ADMIN_USER_IDS` set.
- A ZIP attached to `/update`, sent as an admin document, or replied to with `/update` uses the same handler. Non-admin users cannot start extraction.
- The archive is downloaded asynchronously to `/tmp/bot_update.zip`, size-limited, and checked with `zipfile.is_zipfile()` before extraction. Optional configured malware scanning still runs before extraction.
- Archive entries are checked for path traversal, absolute/drive paths, backslashes, symlinks/special files, encryption, duplicates, size, count, depth, length, and compression ratio. Files are prepared in a temporary project-root directory before replacement. Write failures attempt rollback from copies of the original files.

## Protected state and operational limits

`.env` and `.env.*`, all recognized SQLite/database files and sidecars, `stickers_store.db`, the configured database path and its parent, and persistent root directories such as `data/`, `uploads/`, `backups/`, `instance/`, `storage/`, `logs/`, and `.telegram-zip-updates/` are skipped. The updater also skips virtual environments and common cache folders. It never deletes files missing from the archive.

The process replaces itself with `os.execv(sys.executable, [sys.executable] + sys.argv)` after sending the success message and deleting `/tmp/bot_update.zip`. Extraction and restart exceptions are reported to the update chat. If the process or host cannot send Telegram messages, delivery is not guaranteed. There is no independent health check or automatic service-level rollback in this command. Keep a verified recovery copy and make sure dependencies required by the new code are already installed.

Do not treat ZIP path validation as code review or malware analysis. The archive is trusted code and can change application behavior, configuration examples, deployment scripts, or dependencies. Review it before upload.
