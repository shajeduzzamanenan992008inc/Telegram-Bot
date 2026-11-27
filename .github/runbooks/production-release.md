# Production Release Runbook

## Preconditions
- A reviewed change is merged to the protected release branch/tag.
- Required CI, security, dependency, and configuration checks passed for the exact commit.
- Release artifact identity and provenance are verified.
- Database/data migrations, backups, and recovery plan are reviewed.
- No unresolved critical/high risk is accepted without an accountable owner and documented approval.
- Production environment restrictions and required reviewers are configured.

## Release
1. Confirm target environment, commit SHA, release version, artifact digest, and change summary.
2. Verify the artifact was built from the exact tested commit.
3. Obtain required approval and deploy through the approved pipeline/identity.
4. Monitor deployment progress, infrastructure health, application health, and error budgets.
5. Run authorized smoke tests against verified health endpoints and critical user journeys.
6. Confirm background workers, queues, data integrity, scheduled tasks, and external integrations.

## Rollback / recovery
- Use a known-good artifact and the documented rollback procedure.
- Consider schema/data compatibility before rolling application code back; database rollback may not be safe after new writes.
- Prefer a compatible forward fix when rollback would damage data.
- Record decision, approver, artifact, timestamps, health evidence, residual risks, and user communication.

A green CI run is not proof that a live service is healthy. Record build, deployment, and post-deployment verification as separate signals.
