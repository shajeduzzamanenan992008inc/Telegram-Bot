# Deployment and rollback boundary

The Telegram evolution workflow cannot change `/opt/enan-ultra`, invoke systemd, run an arbitrary deployment command, or claim a service restart succeeded. `/evolve_deploy_approve` records the distinct admin decision for an exact patch hash; the configured operator release procedure remains the deployment mechanism.

Before a manual release, use the normal deployment procedure, verify a known-good backup/commit, apply only the reviewed patch, run the project's tests, and perform an explicit service health check. If health checks fail, stop rollout and restore the known-good release/database backup using the documented operator procedure. Database migrations may not be safely reversible; use a verified backup and the migration-specific recovery plan.

No auto-rollback or production health-monitoring policy is configured by this feature. Do not announce success until the operator deployment and health checks actually succeed.
