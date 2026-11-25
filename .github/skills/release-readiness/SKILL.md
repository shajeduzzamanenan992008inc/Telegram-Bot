---
name: release-readiness
description: Run a pre-release readiness review for a project release candidate.
disable-model-invocation: true
---

Review the requested release candidate. Do not deploy.

Verify:
- exact commit SHA, intended branch/tag, and working-tree state;
- actual CI, test, security, dependency, and configuration results;
- build artifact identity, digest, provenance, and source commit;
- database/schema migrations and forward/rollback safety;
- authentication, authorization, privacy, and domain invariants relevant to the change;
- secrets/configuration handling and environment isolation;
- production environment restrictions, reviewers, and deployment identity;
- monitoring, health checks, runbooks, incident escalation, and rollback/recovery.

Return GO / CONDITIONAL GO / NO-GO with evidence, blockers, owners, and explicitly unverified items. Never convert missing evidence into a pass.
