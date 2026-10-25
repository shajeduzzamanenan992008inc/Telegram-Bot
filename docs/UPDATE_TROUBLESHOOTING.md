# Telegram ZIP update troubleshooting

## `/update` is rejected

- The sender must be in `ADMIN_USER_IDS` or match the separately configured `ADMIN_ID`; the upload must arrive in `ADMIN_CHAT_ID`.
- Run `/update status`; ZIP uploads are disabled by default. Use `/update enable` to open one upload slot for `UPDATE_UPLOAD_WINDOW_SECONDS` (default 300 seconds, range 30–900), then send one ZIP. `/update disable` or `/update cancel` closes an unused window.
- A status of `processing` rejects duplicate uploads. If the bot restarts during processing, startup recovery closes that interrupted gate; an expired open window also closes. If SQLite gate state cannot be checked, upload fails closed.
- Confirm the upload fits `MAX_UPLOAD_BYTES` and the archive entries fit the configured file, path, expansion, and compression-ratio limits.

## Malware scan fails or times out

Every update ZIP requires a successful ClamAV scan even when general upload scanning is disabled. Check that `MALWARE_SCAN_ENABLED` and `CLAMAV_SOCKET_PATH` name the intended configuration, then have the host operator check the `clamav-daemon` service and Unix-socket access. The bot fails closed on a missing/unreachable socket, timeout, incomplete response, or scanner error. It does not bypass scanning. Failed archives remain in private quarantine for follow-up and are removed by retention cleanup.

## Archive validation or staging fails

Check the safe error category in the admin reply and service log. Common causes include corrupt ZIP data, traversal/absolute paths, Windows separators, duplicate or case-colliding paths, symlinks/special files, encrypted entries, protected paths, excessive depth/size/file count, or a file/directory conflict with the current source. `UPDATE_EXTRACT_TIMEOUT_SECONDS` bounds ZIP inspection and extraction.

The upload is a patch: any allowlisted files omitted from the ZIP are copied into the staged candidate from the current source. Omission does not delete a file. Dependencies, database migrations, protected update/security controls, and persistent state cannot be changed through the broker.

## Tests show `BLOCKED`

The staged candidate requires the configured rootless Docker sandbox. Check `CODE_SANDBOX_ENABLED`, `CODE_SANDBOX_IMAGE`, and the sandbox service availability. A disabled or unavailable sandbox is not a test pass; the bot refuses staging approval. `UPDATE_TEST_TIMEOUT_SECONDS` controls the isolated update test deadline.

## Deployment approval is unavailable or fails

Upload permission, staging review, and production deployment are separate decisions. Production approval is available only when `UPDATE_DEPLOYMENT_ENABLED=true`, the root-owned broker passes target/ownership/environment/service checks, `/etc/telegram-ai/zip-deploy.enabled` exists, and the restricted Unix socket is available. The Ubuntu installer intentionally leaves the broker socket and enable flag disabled. By default, a separate root operator authorization is required. Automatic authorization is available only when the broker's root-controlled process environment explicitly configures `TELEGRAM_AI_AUTO_DEPLOY_ADMIN_IDS`; empty/unset means manual approval, malformed input prevents broker startup, and the allowlist must match an allowed Telegram callback admin ID.

The broker records a verified source snapshot before activation, creates an immutable versioned release, atomically switches `/opt/enan-ultra/current`, restarts only `telegram-ai.service`, and checks a fresh PID-bound health marker, database readiness, import origin, and Telegram bot identity. If health fails, it switches back to the prior release and checks it again. `rollback_failed` means automated recovery was not verified; the host operator must inspect the protected deployment journal and systemd journal and recover manually. Bot notification may be unavailable if both candidate and rollback fail to start.

These broker checks have local mocked test coverage only. The live Azure service identity, Linux/systemd behavior, backups, health checks, and rollback have not been verified. Keep production deployment **NO-GO** until the target and a disposable Linux recovery drill are independently verified. See [deployment and rollback](./DEPLOYMENT_AND_ROLLBACK.md).
