# Telegram ZIP updates

`/update` stages an administrator-supplied ZIP for review. It never writes into the running checkout. Production activation is a separate action through the optional, disabled-by-default systemd deployment broker.

## Upload, review, and approval

1. Start `/update` as the user identified by `ADMIN_ID` in the chat identified by `ADMIN_CHAT_ID`. The upload session lasts five minutes. Send one `.zip` document to that chat.
2. The bot enforces `MAX_UPLOAD_BYTES`, places the upload in a private quarantine directory, and requires a successful ClamAV scan even when general upload scanning is disabled. A malware result, scanner error, or timeout fails closed. Failed scan/validation archives remain in quarantine for administrator follow-up and expire during cleanup.
3. The staged manager validates the archive and protected paths, computes SHA-256 hashes and a deterministic release manifest, and extracts to an isolated staging directory. ZIP inspection and extraction are bounded by `UPDATE_EXTRACT_TIMEOUT_SECONDS`.
4. Python syntax checks run against the staged files. The project test suite must also pass in the configured rootless Docker sandbox. If the sandbox is disabled or unavailable, tests are marked `BLOCKED`; the bot does not claim a pass and staging approval is refused.
5. Review the bot's preview, including the release digest, file actions, security notes, syntax status, and isolated-test result. `Approve staging only`, `Reject`, and `Cancel` apply to that exact digest. A changed artifact invalidates a callback. Review data and approvals are process-local and expire after 30 minutes; a bot restart clears the active review state.
6. The separate `Approve production deployment` action queues the same update ID, manifest SHA-256, archive SHA-256, and fixed deploy operation; it does not activate the release. The bot's administrator ID in this request is an untrusted claim. Activation additionally requires a root operator to run `sudo /usr/local/libexec/telegram-ai-deploy --authorize <UPDATE_ID>` from an interactive terminal, verify the actual admin approval in Telegram, and confirm the full digest. Broker authorization expires after 15 minutes, is one-time, and is stored in root-private state. The checked-in default is disabled and the installer leaves the broker socket disabled.

Every action is restricted to the configured update administrator and administrator chat. The bot audits staging and approval decisions without recording secret values.

## What can be deployed

The review package may include application source, tests, and documentation for inspection. The broker activates only permitted `src/enan_ultra/` files. It rejects dependencies, database migrations, protected update/security/runtime control files, and persistent-state paths. An uploaded ZIP is a patch: files it omits are retained from the running allowlisted source tree, and ZIP uploads do not delete files. `.env`, database files and SQLite sidecars, uploads, stickers, backups, and runtime state are outside the release tree and remain untouched.

The broker socket request from the service UID cannot activate code. It only creates a root-owned pending request. Root-only `approval_details` and `authorize` socket operations reject all non-root peers; the exact request must still be pending and unexpired, and the broker consumes it atomically before starting activation. The operator CLI's manual Telegram review is a trust assumption, not machine-verifiable proof. Root remains a trusted release authority.

The broker builds an immutable directory under `/opt/enan-ultra/releases/<digest>/`, verifies its files against the approved manifest, then atomically switches `/opt/enan-ultra/current`. It restarts only `telegram-ai.service`. A fresh PID-bound health marker must report the active release import origin, an open database, and the same Telegram bot identity. If the candidate fails, the broker switches back to the verified prior release and checks it again. `rollback_failed` means recovery was not verified and needs manual host-side recovery.

This broker layout is a local design for `telegram-ai.service`, user/group `enan-ultra`, and `/opt/enan-ultra`. The historical Azure target in the readiness report was a different service and path; its current identity has not been independently rechecked. Do not enable the broker on a host until its target, ownership, service unit, state paths, and recovery process have been verified there.

## Configuration names

Relevant settings are `ADMIN_ID`, `ADMIN_CHAT_ID`, `ADMIN_USER_IDS`, `MALWARE_SCAN_ENABLED`, `CLAMAV_SOCKET_PATH`, `CLAMAV_SCAN_TIMEOUT_SECONDS`, `MAX_UPLOAD_BYTES`, `MAX_ARCHIVE_EXPANDED_BYTES`, `UPDATE_MAX_FILES`, `UPDATE_MAX_DEPTH`, `UPDATE_MAX_PATH_LENGTH`, `UPDATE_MAX_COMPRESSION_RATIO`, `UPDATE_EXTRACT_TIMEOUT_SECONDS`, `UPDATE_TEST_TIMEOUT_SECONDS`, `CODE_SANDBOX_ENABLED`, `CODE_SANDBOX_IMAGE`, `CODE_SANDBOX_TIMEOUT_SECONDS`, `CODE_SANDBOX_MEMORY_MB`, `CODE_SANDBOX_CPU_COUNT`, `CODE_SANDBOX_MAX_FILES`, `CODE_SANDBOX_MAX_EXPANDED_BYTES`, `UPDATE_DEPLOYMENT_ENABLED`, and `UPDATE_DEPLOYMENT_SOCKET_PATH`. See `.env.example` and [configuration](./CONFIGURATION.md) for names and defaults; keep credential values out of ZIPs and reports.
