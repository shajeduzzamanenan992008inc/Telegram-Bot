# Database Migration Runbook

## Before the change
- Confirm target environment, source/target schema, database version, migration tool, and affected services.
- Review the exact migration diff, locks, data volume, indexes, constraints, and compatibility with old/new application versions.
- Confirm verified backups and a recent restore test; record applicable RPO/RTO.
- Define pre-checks, stop conditions, validation queries, rollout, and forward-recovery/rollback strategy.
- Obtain explicit production approval.

## Execution
- Deploy in a controlled window using the approved identity and exact reviewed commit.
- Prefer additive expand-migrate-contract steps for critical systems.
- Monitor duration, lock waits, errors, replication/lag, resource use, and application health.
- Stop on unexpected data loss, lock contention, excessive latency, or failed invariants.

## After the change
- Verify schema version, row counts/invariants, indexes, application compatibility, and critical user flows.
- Record migration ID, commit, operator, approval, timestamps, result, and evidence.
- Keep rollback/forward-fix instructions and recovery owner available until the change is stable.
