# Mission Control architecture

`MissionControl` is a small async orchestration service over the existing provider router and SQLite database. A multilingual model creates a bounded plan; the executor then runs each step as a separate no-tools AI analysis request. There are no dynamically registered tools and no arbitrary shell, Python, filesystem, deployment, payment, or device actions.

## Lifecycle

`planning → running → completed`

`planning/running → paused → running`, or active state → `failed`, `cancelled`, or `expired`.

The database persists encrypted objective, plan, completed notes, owner/chat IDs, step counts, state, error class, timestamps, and duplicate fingerprint. Each step result is committed before the next step begins. Provider requests reserve the normal per-user/global usage budget and obey the provider router's free-only, local-only, fallback, retry, and per-request attempt policies.

## Limits and access

- Private Telegram chats only; owner-scoped read/control.
- Up to 3 active missions per owner; global concurrency uses `AGENT_MAX_WORKERS`.
- Step count is bounded by `AGENT_MAX_STEPS` (maximum 12); ready independent steps run up to `AGENT_MAX_WORKERS` at once. Cost uses `AGENT_MAX_COST_USD` split conservatively across planning and steps.
- A mission expires after 30 days. Duplicate active requests return the existing mission ID.
- Restart recovery pauses all incomplete missions. No side effect is replayed because no side-effecting tool exists; owner explicitly resumes informational work.

## Limitations

The model can reason only over the task text and previous generated notes. It cannot inspect this server's project files, run tests, browse the web, access a document from a previous message, or verify an external action. Upload/document analysis remains in existing handlers. Missions are analysis workflows, not a general autonomous agent.
