# Telegram ZIP update troubleshooting

## `/update` is rejected

- Confirm that `.env` contains the intended positive numeric `ADMIN_ID`. This ID must match the Telegram sender exactly. If `ADMIN_ID` is unset, the first legacy admin is the lowest numeric ID in `ADMIN_USER_IDS`.
- Send a Telegram document whose filename ends in `.zip`. You can attach it to `/update`, reply to it with `/update`, or send it directly from the configured admin account.
- The compressed file must fit within `MAX_UPLOAD_BYTES` and pass `zipfile.is_zipfile()`.

## Archive extraction fails

Check the Telegram error category and bot logs. Common causes include an invalid/corrupt ZIP, traversal or absolute paths, Windows separators, duplicate names, symlinks, encryption, excessive entry count/depth/path length/expanded size/compression ratio, or a collision with an existing file/directory. Configure the existing archive limits in `.env` if a trusted package legitimately exceeds them.

Members that target protected configuration or persistent state are skipped, including `.env*`, database files, the configured database parent directory, and the persistent folders documented in [Telegram ZIP updates](./TELEGRAM_ZIP_UPDATES.md). No files are deleted when absent from an archive. Dependency installation and database migrations are not run.

## Restart fails or Telegram stops responding

The updater calls `os.execv()` after it sends the success message. If process replacement raises an exception, the handler attempts to notify the admin with an error. A host outage, invalid runtime environment, or Telegram connectivity issue may prevent delivery. Check the service manager and logs from the host, confirm required dependencies are installed, and restore the last verified code copy if the process does not return. This command has no independent health check or automatic service-level rollback.
