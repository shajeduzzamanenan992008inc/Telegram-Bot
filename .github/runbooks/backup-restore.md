# Backup and Restore Runbook

## Backup policy
- Define protected assets, data owners, backup scope, frequency, retention, encryption, access roles, and offsite/immutable copies where required.
- Keep backup credentials separate from application credentials and restrict restore permissions.
- Record backup job status, integrity verification, failures, and retention expiry.

## Restore procedure
1. Obtain incident/change authorization and identify the restore point and target environment.
2. Verify backup integrity and provenance before restore.
3. Restore to an isolated target first when practical; protect existing data and avoid accidental overwrite.
4. Validate schema, data integrity, dependent services, access controls, and application health.
5. Reconcile writes/events created after the restore point using an approved process.
6. Record recovery time, data loss window, verification evidence, and deviations from RPO/RTO.

A successful backup job is not proof of recoverability. Perform scheduled restore tests and record outcomes.
