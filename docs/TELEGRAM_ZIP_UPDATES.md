# Telegram ZIP updates

The admin `/update` command applies a ZIP package to the current project checkout and replaces the running Python process. Send a `.zip` document with caption `/update`, run `/update` while replying to a ZIP document, or send a ZIP document from the configured admin account. A plain `/update` message explains the upload options.

## Authorization and operation

- The update handler accepts only the Telegram user whose ID equals `ADMIN_ID` from `.env`. For older configurations that omit `ADMIN_ID`, startup uses the lowest numeric ID in `ADMIN_USER_IDS` as the update administrator. Set `ADMIN_ID` explicitly to avoid ambiguity.
- Telegram downloads the document asynchronously to `/tmp/bot_update.zip`. The handler enforces `MAX_UPLOAD_BYTES` and checks the archive with `zipfile.is_zipfile()` before extraction.
- After sending `📦 Extracting update package...`, the bot validates archive paths and size limits, extracts into a temporary directory under the project root, and replaces included files. It sends `✅ Code updated successfully! Restarting bot process...`, removes the downloaded archive, and calls `os.execv()` with the current Python executable and arguments.
- Extraction rejects absolute/traversal paths, backslashes, drive-like paths, symlinks/special files, encrypted entries, duplicate/case-colliding paths, excessive path depth/length, too many files, excessive expanded size, and excessive compression ratios. Existing symlink targets are rejected. The archive is staged before changes are applied, and ordinary write failures trigger rollback of files already replaced.
- The updater does not delete project files absent from the ZIP and does not install dependencies or run migrations. Include repository-root paths in the ZIP; archive paths are preserved under the project root.

## Files preserved

Archive members under protected paths are skipped and never written. This includes `.env` and `.env.*`, SQLite/database files and sidecars, `stickers_store.db`, the configured `DATABASE_PATH` and its parent state directory, and the root state folders `data/`, `uploads/`, `backups/`, `instance/`, `storage/`, `logs/`, `.git/`, and `.telegram-zip-updates/`. Virtual environments and common cache directories are also skipped.

Keep a verified code release and recovery copy before applying an update. The updater can report extraction or process-restart errors to Telegram, but a machine or service failure can prevent Telegram delivery. The code update takes effect immediately after restart; ensure the archive contents match the running checkout's expected project layout and installed dependencies.

## Configuration

Set `ADMIN_ID` to one positive Telegram user ID in `.env`. `ADMIN_USER_IDS` continues to authorize the rest of the bot's existing admin commands. Existing archive limits are controlled by `MAX_UPLOAD_BYTES`, `MAX_ARCHIVE_EXPANDED_BYTES`, `UPDATE_MAX_FILES`, `UPDATE_MAX_DEPTH`, `UPDATE_MAX_PATH_LENGTH`, and `UPDATE_MAX_COMPRESSION_RATIO`.
