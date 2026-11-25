---
applyTo: ".github/workflows/*.{yml,yaml}"
---

# GitHub Actions engineering instructions

- Use least-privilege workflow and job permissions; prefer `contents: read` and scope write access to the one job that needs it.
- Pin third-party actions to a verified full-length commit SHA or an approved immutable workflow lockfile; include a release comment and verify the SHA against the official upstream repository.
- Keep action versions updated through Dependabot, then review upstream changes before merging.
- Do not use `pull_request_target` to execute untrusted code. Do not expose secrets to fork/pull-request code.
- Treat privileged `workflow_run` workflows as a separate trust boundary; never check out or execute untrusted PR code in a privileged context.
- Set job timeouts, concurrency, explicit artifact names, appropriate retention, and `persist-credentials: false` when checkout credentials are unused.
- Do not echo secrets, enable shell tracing around secrets, or put credentials in artifacts, caches, or job outputs.
- Production deployment must use the exact verified artifact/commit, a protected environment, short-lived identity where possible, and a documented rollback/recovery path.
- Review workflow triggers, expressions, contexts, permissions, artifacts, and shell quoting as security-sensitive code.
