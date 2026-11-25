---
name: repository-deep-audit
description: Evidence-first repository audit across source, architecture, dependencies and operations.
disable-model-invocation: true
---

Perform a read-only, evidence-first audit of this repository.

1. Confirm repository root, branch, working tree, and instruction files.
2. Map actual architecture, product capabilities, modules, interfaces, and data flows; verify claims in source.
3. Inventory applications, packages, lockfiles, database schemas/migrations, APIs, tests, workflows, infrastructure, security controls, and integrations.
4. Identify critical user/system journeys and trust boundaries for the actual product domain.
5. Review security, privacy, architecture, quality, accessibility, performance, reliability, maintainability, and operational risks proportionate to project criticality.
6. Do not edit files or run destructive commands during a read-only audit.

Return a prioritized report with evidence paths, confidence, dependencies, quick wins, high-risk work, verification gaps, and phased roadmap. Separate confirmed facts from unknowns.
