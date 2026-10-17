# ZIP update security boundaries

The ZIP workflow separates unprivileged Telegram-side staging/approval from a root-owned local deployment broker. The bot has no sudo rule and does not run shell commands, migrations, dependency installers, or code from the ZIP. Only the broker may write the fixed application path or restart the one fixed systemd service.

## Authorization and approval

- `/update` and `/update_cancel` require a configured administrator and `ADMIN_CHAT_ID`.
- A pending upload is bound to the initiating user and exact chat, accepts one document, and expires after five minutes.
- Each callback rechecks administrator identity, blocked-user status, and the configured chat.
- Approval is a one-shot decision for an exact manifest SHA-256, with server-side verification of the staged archive and every release file. Stale, replayed, changed, or expired releases cannot be approved.
- Staging approval is never production approval. Production requires an independent callback, a currently enabled operator configuration, a ready broker preflight, and exact release/manifest/archive hashes.
- The broker accepts only systemd socket activation from the expected `enan-ultra` Unix-socket peer. It checks the fixed service name, service user, working directory, executable and module entry point. A service-layout mismatch or missing root-owned enable flag blocks deployment.
- The broker independently authenticates the local bot service account, not the Telegram administrator. Administrator authorization and production approval are enforced by the bot before it submits a request; `administrator_id` is audit attribution, not a cryptographic proof. A compromised bot process could submit a broker request, though the broker still restricts paths, hashes, validation state, service action, and persistent data. Do not treat this design as safe against compromise of the bot process itself.
- A root-owned broker journal consumes a release ID once, takes a process and OS lock, and records the manifest hash, administrator ID, timestamp, snapshot, health result and final state.
- Deployment control files (`config.py`, `handlers.py`, `runtime.py`, `security.py`, `storage/database.py`, and `telegram_zip_updates.py`) and dependency manifests are not updateable through the bot. Protecting `storage/database.py` prevents a ZIP release from silently changing schema/migration behavior. Dependency or database-layer changes require a separate operator-reviewed release and recovery plan.

## ZIP validation

Limits come from `MAX_UPLOAD_BYTES`, `MAX_ARCHIVE_EXPANDED_BYTES`, `UPDATE_MAX_FILES`, `UPDATE_MAX_DEPTH`, `UPDATE_MAX_PATH_LENGTH`, and `UPDATE_MAX_COMPRESSION_RATIO`. Entry names are Unicode-normalized and checked for absolute/traversal paths, Windows separators and reserved names, duplicate or case-insensitive collisions, file/directory conflicts, protected paths, and allowlist membership. Symlinks and special files, encrypted members, unsupported compression, corrupt members, size mismatches, and excessive expansion are rejected.

Every member is read in bounded chunks and SHA-256 hashed before extraction begins. The staged extraction uses those prevalidated entries, exclusive file creation, a dedicated random release directory, and restrictive POSIX permissions. File content and the complete release manifest are rechecked before tests and approval.

The allowlist is intentionally narrow: application package source, tests, docs, and named project metadata only. Secret/configuration files, databases and backups, generated/runtime data, user uploads, deployment files, scripts, and unrelated repository files are not updateable. Unexpected paths are rejected rather than silently skipped.

## Test isolation

Python syntax validation calls `compile()` on candidate bytes; it does not import or execute them. Running project tests is arbitrary candidate-code execution and is therefore optional, disabled unless explicitly configured, and delegated to the existing rootless Docker sandbox with no network, constrained resources, and a disposable filesystem. The archive is not used to install dependencies. If the sandbox cannot be verified or tests cannot run, the result is `BLOCKED` or `FAIL`, and staging approval is denied.

The sandbox reduces risk but is not a proof that arbitrary tests are harmless. Keep the sandbox image locally built and trusted, rootless, patched, and unavailable to untrusted users. Do not enable this workflow for untrusted administrators.

## Data and operational limits

The stage directory is placed beside `DATABASE_PATH`, under a dedicated directory; it is not stored in the live application package. The bot process retains pending sessions in memory and does not persist the uploaded ZIP into application history. A restart invalidates in-memory upload and review state. Audit events retain only update IDs, outcomes, and truncated hash fingerprints, not archive data or secrets.

The broker independently revalidates the bounded archive, manifest and hashes, mandatory validation statuses, allowed paths, and the old live source hashes. It snapshots regular `src/enan_ultra` source files; it never touches `.env`, database files, uploads, sticker/user data, systemd unit files or runtime state. It verifies the snapshot before activation and will stop at the five-snapshot/500 MiB retention limit rather than delete recovery points.

After restart, the broker checks systemd state/PID and a new runtime marker written only after the database `SELECT 1` and Telegram `getMe` initialization succeed. It does not send user messages or consume AI-provider credits. Code activation is an individually atomic file replacement, not an atomic symlink switch; interrupted application is journaled and the next broker start restores the verified snapshot before accepting requests. A failed health check triggers rollback and another restart/health check. A failed rollback is explicitly reported as `rollback_failed`; no success claim is made.

The manifest is not a security scanner. Sandbox tests execute candidate tests inside the configured offline rootless container, but do not prove production correctness. Passing syntax/tests does not validate dependency safety, every command/provider, schema migration safety, service health beyond the stated checks, or production readiness. Database migrations are not run and never automatically reversed. Keep deployment disabled until the real service layout and operator recovery procedure have been checked.
