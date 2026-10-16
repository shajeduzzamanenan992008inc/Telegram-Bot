# Self-evolution proposal workflow

## Current flow

1. An authorized user replies to a project ZIP with `/patch <change request>`.
2. The configured model returns a unified diff. A path validator rejects traversal, binary changes, symlink changes, and protected security/configuration/database/handler/sandbox/deployment paths from automated review.
3. In private chat, the bot repacks only bounded project files and excludes secret-named files/caches, then stores the exact diff and sanitized source ZIP encrypted with SHA-256 hashes, creator, test report, state, and a seven-day expiry. A generic admin notice contains only the proposal ID.
4. If the rootless sandbox is configured, tests apply the patch to a disposable copy and run the project's pytest suite with networking disabled. Otherwise an admin can run `/evolve_test ID` after the sandbox is configured.
5. An admin inspects `/evolve_review ID` and the exact diff. `/evolve_approve ID PATCH_SHA256` requires a passing test result and records approval for sandbox staging only.
6. `/evolve_stage ID PATCH_SHA256` replays the exact stored patch against the exact stored source ZIP in the isolated runner. A passing result opens the separate deployment-approval state.
7. `/evolve_deploy_approve ID PATCH_SHA256` records a second admin approval. It does not deploy or restart the production service.

Failed tests, an expired proposal, a stale state, a non-admin sender, a detected credential literal, or any hash mismatch blocks the transition. State updates use a conditional SQLite update, so a concurrent/replayed approval can succeed only once. The patch and source archive are erased on cancellation/expiry; terminal metadata is periodically cleaned.

## State sequence

`testing → awaiting_approval → approved_staging → awaiting_deployment_approval → deployment_approved`

Terminal alternatives are `failed`, `rejected`, `cancelled`, and `expired`. `deployment_approved` is an audit record, not a claim that deployment happened.

## Not automated

There is no configured staging service, release repository, signed artifact pipeline, production file replacement, systemd restart, health-check rollout, or automatic rollback adapter in this deployment. Automated evolution also refuses dependency manifests and protected security/configuration/database/handler/sandbox/deployment paths. The bot therefore stops at recorded production approval. Operators must use the project's documented release procedure and verify/rollback manually. It never writes a candidate into the running source directory.
