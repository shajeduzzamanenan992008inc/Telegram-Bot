# Self-evolution proposal workflow

## Current flow

1. An administrator controls proposal collection with `/evolve_mode on|off|status`. It defaults to `on` to preserve existing behavior. When off, `/patch` can still return a suggested diff, but does not store or stage an evolution proposal. `/learning_pause` remains the broader switch for feedback and improvement-signal collection.
2. With proposal mode on, an authorized user replies to a project ZIP with `/patch <change request>`.
3. The configured model returns a unified diff. A path validator rejects traversal, binary changes, symlink changes, and protected security/configuration/database/handler/sandbox/deployment paths from automated review.
4. In private chat, the bot repacks only bounded project files and excludes secret-named files/caches, then stores the exact diff and sanitized source ZIP encrypted with SHA-256 hashes, creator, test report, state, and a seven-day expiry. A generic admin notice contains only the proposal ID.
5. If the rootless sandbox is configured, tests apply the patch to a disposable copy and run the project's pytest suite with networking disabled. Otherwise an admin can run `/evolve_test ID` after the sandbox is configured.
6. An admin inspects `/evolve_review ID` and the exact diff. `/evolve_approve ID PATCH_SHA256` requires a passing test result and records approval for sandbox staging only.
7. `/evolve_stage ID PATCH_SHA256` replays the exact stored patch against the exact stored source ZIP in the isolated runner. A passing result opens the separate deployment-approval state and stores the bounded candidate ZIP, with its SHA-256, in the encrypted proposal payload.
8. An administrator can download the exact candidate bytes with `/evolve_export ID PATCH_SHA256 candidate`. The export is private-chat only, checks the proposal state, patch hash, archive limits, and candidate hash, and records an audit event. The archive sent is byte-for-byte the one whose candidate hash was recorded. `source` exports the sanitized input ZIP for this proposal; it is not a verified production rollback snapshot.
9. `/evolve_deploy_approve ID PATCH_SHA256` records a second admin approval. It does not deploy or restart the production service.

Failed tests, a missing/oversized candidate, an expired proposal, a stale state, a non-admin sender, a detected credential literal, a candidate containing excluded files, or any hash mismatch blocks the transition or export. State updates use a conditional SQLite update, so a concurrent/replayed approval can succeed only once. The patch and stored archives are encrypted and erased on cancellation/expiry; terminal metadata is periodically cleaned.

## State sequence

`testing → awaiting_approval → approved_staging → awaiting_deployment_approval → deployment_approved`

Terminal alternatives are `failed`, `rejected`, `cancelled`, and `expired`. `deployment_approved` is an audit record, not a claim that deployment happened.

## Not automated

There is no configured staging service, release repository, signed artifact pipeline, production file replacement, systemd restart, health-check rollout, or automatic rollback adapter in this deployment. Automated evolution also refuses dependency manifests and protected security/configuration/database/handler/sandbox/deployment paths. The bot therefore stops at a downloadable, verified candidate and recorded production approval. Operators must review and apply the candidate through the project's documented release procedure and verify/rollback manually. It never writes a candidate into the running source directory.
