# Approval and permissions

Mission reports are visible only to their Telegram owner. Evolution administration requires a sender whose numeric ID is in `ADMIN_USER_IDS`; every handler checks this at execution time.

Evolution approvals bind to proposal ID, exact patch SHA-256, current state, test pass, and expiry. Staging approval requires a passing isolated test. A second sandbox verification is required before the separate production approval is accepted. Updates are single-use conditional SQL writes, so replay and concurrent calls fail after the first successful transition.

Production approval is recorded for audit only. No deployment adapter exists, so it cannot modify files or restart a service. To add deployment later, configure a narrowly scoped staging and release backend with immutable artifact digests, a separate privileged approval step, health checks, rollback, and tests; never grant arbitrary shell access to the model.
