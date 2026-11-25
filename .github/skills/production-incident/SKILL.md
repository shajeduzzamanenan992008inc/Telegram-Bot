---
name: production-incident
description: Triage a production incident safely and preserve evidence.
disable-model-invocation: true
---

Triage the supplied production incident.

First establish impact, affected workflows/services, time window, environment, current release identity, recent changes, symptoms, and available logs/metrics. Separate observed facts from hypotheses. Protect private data and credentials.

Recommend containment before speculative repair. Prefer bounded read-only investigation and reversible mitigations. Do not run destructive commands, alter production settings, rotate credentials, restore backups, or deploy fixes without explicit authorization.

Return impact, timeline, evidence, containment options, risk, next verification steps, owner, rollback/recovery decision tree, and a concise communications update.
