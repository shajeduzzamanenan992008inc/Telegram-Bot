---
applyTo: "**/tests/**,**/__tests__/**,**/*test*.*,**/*spec*.*,**/*_test.go,**/*_test.rs,**/test_*.py"
---

# Testing and quality instructions

- Discover the actual test framework, fixtures, scripts, environment, and supported runtime versions first.
- Test observable behavior and domain invariants, not only implementation details.
- Include positive, negative, boundary, authorization, concurrency, retry, and partial-failure cases proportionate to risk.
- Keep tests deterministic, isolated, repeatable, and based on synthetic data.
- Do not use real production data or live payment/external side effects in automated tests unless explicitly authorized and sandboxed.
- Do not delete, weaken, skip, or broaden test exclusions merely to obtain a green run.
- Never report a test as passed unless its command completed successfully; list tests not run and why.
