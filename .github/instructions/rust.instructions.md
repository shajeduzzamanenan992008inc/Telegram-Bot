---
applyTo: "**/*.rs,**/Cargo.toml,**/Cargo.lock,**/rust-toolchain*"
---

# Rust engineering instructions

- Respect the declared MSRV, edition, workspace structure, feature flags, and dependency policy.
- Prefer explicit ownership and borrowing; do not introduce unsafe code unless essential and documented with a narrow safety rationale.
- Avoid panics in recoverable runtime paths; use structured error types and propagate failures intentionally.
- Bound concurrency, channels, allocations, and external input sizes.
- Keep Cargo.lock consistent for applications and follow workspace conventions for libraries.
- Run formatting, clippy, tests, and relevant feature/build matrices.
