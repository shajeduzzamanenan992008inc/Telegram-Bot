---
applyTo: "**/*.{py,js,jsx,ts,tsx,java,kt,go,rs,cs,php,rb,css,scss,html},**/static/**,**/templates/**"
---

# Performance and reliability instructions

- Measure before optimizing and preserve the environment, workload, baseline, and method used for comparison.
- Inspect query count/plans, unbounded pagination, synchronous slow I/O, repeated work, oversized assets, memory growth, and expensive rendering where relevant.
- Use timeouts and bounded retries with backoff/jitter for unreliable dependencies.
- Consider concurrency limits, backpressure, caching and invalidation, graceful degradation, and recovery where justified.
- Keep reliability and correctness ahead of micro-optimizations.
- Do not claim performance improvement without comparable measurements and disclose trade-offs.
