# Resource budgets

Mission Control reuses `AGENT_MAX_STEPS`, `AGENT_MAX_WORKERS`, and `AGENT_MAX_COST_USD`. Each user may have at most three active missions. Provider calls are reserved through the existing user daily/monthly request and cost limits, the bot-wide daily cost ceiling, provider attempt cap, free-only policy, and provider timeout. A mission is bounded to at most 12 steps and provider calls are individually timed out.

Evolution proposals are capped at 10 active proposals globally and 3 per creator, with a seven-day expiry. Stored source ZIPs are at most 20 MiB, patches at most 1 MB, and touched paths at most 20 files. ZIP extraction uses configured archive limits, and the isolated runner uses configured timeout/CPU/RAM/PID bounds. A second sandbox run is required after staging approval.

Maintenance erases encrypted mission details at 30 days and proposal patch/source payloads at seven days, then prunes terminal metadata 90 days after its last state update. Mission and evolution data is included in encrypted database backups, so backup retention must be considered when handling deletion requests.
