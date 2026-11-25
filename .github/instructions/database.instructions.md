---
applyTo: "**/*{model,schema,migration,repository,database,db,query}*.{py,js,ts,java,kt,go,rs,cs,php,rb},**/migrations/**,**/*.sql,**/*.{prisma,graphql}"
---

# Database and data engineering instructions

- Confirm the database engine, ORM/query conventions, migration history, data ownership model, and current tests before changing persistence.
- Use constraints for invariants that must hold under concurrency; review indexes, nullability, uniqueness, foreign keys, cascades, and tenant keys.
- Inspect query plans and representative data volumes before claiming a query or index optimization.
- Prefer additive expand-migrate-contract changes for high-risk or rolling deployments.
- Do not edit an applied migration casually; create a new migration consistent with repository conventions.
- Protect sensitive data with least privilege, minimization, retention controls, and privacy-safe logs.
- Never run destructive migrations, truncation, or production data mutation without explicit approval and verified recovery steps.
- Test migration from the supported prior schema and forward/rollback behavior where feasible.
