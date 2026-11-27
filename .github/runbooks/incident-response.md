# Incident Response Runbook

## 1. Activate and assess
- Record incident start time, reporter, affected service/product, environment, symptoms, and customer impact.
- Assign an incident lead and technical/communications roles appropriate to the event.
- Establish a trusted incident channel and an auditable timeline.
- Classify severity using the organization's approved matrix; do not invent severity definitions.

## 2. Stabilize safely
- Preserve evidence and relevant logs while minimizing access to sensitive data.
- Check recent releases, infrastructure changes, dependencies, alerts, capacity, and external providers.
- Prefer reversible mitigations such as disabling an affected feature or rolling back a verified artifact.
- Do not delete evidence, run destructive commands, or make unapproved production changes.

## 3. Investigate and communicate
- Form a hypothesis and test it with bounded, authorized queries.
- Identify affected assets, data, users, integrations, and time window.
- Escalate possible privacy, security, safety, legal, or regulatory incidents through the approved process.
- Communicate confirmed facts, impact, mitigation, and next update time; avoid speculation.

## 4. Recover and learn
- Verify service health, data integrity, backlog reconciliation, and user-facing journeys.
- Document root cause and contributing factors without blame.
- Track corrective actions with owner, priority, due date, and verification method.
- Update monitors, tests, runbooks, and recovery procedures.
