---
applyTo: "**/*.{py,js,cjs,mjs,ts,cts,mts,java,kt,go,rs,cs,fs,php,rb,ex,exs}"
---

# Backend and service engineering instructions

- Trace the existing entry point, routing, middleware, service/domain layer, persistence, background processing, and integration boundaries before editing.
- Keep transport handlers thin and reuse the existing architecture; do not create parallel layers without a demonstrated need.
- Validate schemas and enforce authentication, authorization, ownership, and tenant boundaries at trusted service boundaries.
- Use parameterized database access, explicit transaction boundaries, bounded resource use, and consistent error semantics.
- Define timeout, retry, idempotency, cancellation, and replay behavior for external calls and asynchronous work.
- Keep secrets outside source code and logs. Avoid exposing internal stack traces or private records.
- Preserve API compatibility and version contracts when clients or integrations depend on them.
- Add tests for allowed and denied access, boundary input, failure handling, concurrency, and state transitions.
