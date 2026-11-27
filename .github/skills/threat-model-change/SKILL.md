---
name: threat-model-change
description: Threat-model a project feature, system change, or pull request.
disable-model-invocation: true
---

Create a focused threat model for the specified change.

Identify assets, actors, trust boundaries, entry points, data flows, privileged operations, third-party dependencies, and security assumptions. Consider relevant STRIDE-style categories, broken access control, tenant/resource isolation, sensitive-data exposure, injection, SSRF, CSRF, XSS, replay, file handling, supply-chain risk, and AI/tool misuse where applicable.

For each finding provide severity, confidence, evidence path, exploit preconditions, potential impact, smallest safe mitigation, regression tests, and residual risk. Do not claim exploitability without evidence and do not modify code in a review-only task.
