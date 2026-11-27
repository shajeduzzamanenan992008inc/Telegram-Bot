---
name: implement-feature-safely
description: Implement one project feature as a tested, reviewable vertical slice.
disable-model-invocation: true
---

Implement the requested feature as a bounded vertical slice.

Before editing:
- inspect the current branch, working tree, and repository instructions;
- map the affected modules, interfaces, data, authorization, integrations, and tests;
- identify acceptance criteria, compatibility constraints, and approval boundaries;
- preserve existing product identity and architecture.

Implementation:
- make the smallest coherent change;
- validate inputs and enforce authorization at trusted boundaries;
- handle failure, retries, and state transitions intentionally;
- add or update tests for expected, denied, boundary, and failure behavior;
- update migrations and documentation only when required;
- do not add unrelated refactors or speculative infrastructure.

After editing:
- run the relevant tests, type checks, lint, and builds that exist;
- inspect the full diff and generated files;
- report exact commands/results, remaining risks, and rollback notes;
- do not commit, merge, publish, or deploy without explicit approval.
