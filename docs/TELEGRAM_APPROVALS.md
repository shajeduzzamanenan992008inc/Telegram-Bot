# Telegram approval commands

Every command checks `ADMIN_USER_IDS` by the effective Telegram sender ID. Chat membership, username, message text, and callback data do not grant authority.

## Evolution review

- `/evolve_pending` — show IDs, states, and short patch hashes.
- `/evolve_review ID` — in a private admin chat, inspect summary, source and patch SHA-256, test exit/timeout, expiry, and retrieve the exact diff.
- `/evolve_test ID` — in a private admin chat, run a proposal in the isolated runner before approval.
- `/evolve_approve ID PATCH_SHA256` — approve only the reviewed exact hash for isolated staging; passing tests are mandatory.
- `/evolve_stage ID PATCH_SHA256` — in a private admin chat, repeat tests after approval; a passing result opens the separate production-approval gate.
- `/evolve_deploy_approve ID PATCH_SHA256` — record a separate production approval for the same reviewed hash. It does not deploy.
- `/evolve_cancel ID PATCH_SHA256` — cancel and erase encrypted patch/source content.
- `/evolve_status` — list recent outcomes.

Approval changes are one-shot conditional database updates. Wrong hashes, stale states, expired proposals, failed tests, and non-admin identities are rejected. The current interface uses commands rather than inline buttons, so there are no client-trusted callback payloads.

## Learning controls

`/learning_status`, `/learning_pause`, and `/learning_resume` are administrator-only. The pause blocks feedback intake, automatic fact extraction, and evolution proposals; it leaves existing user-consented memory available and explicit `/remember` under user control.
