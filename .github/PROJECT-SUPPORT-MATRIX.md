# Project Support and Adaptation Matrix

This kit provides common engineering standards and configurable workflows. The support matrix describes the kinds of projects the framework can guide; it does not mean every platform-specific build, emulator, compiler, cloud account, or compliance control is bundled or automatically executed.

| Project category | Supported guidance | CI integration approach | Extra project configuration |
|---|---|---|---|
| Web / frontend | UI, accessibility, performance, browser safety | Add real package-manager commands | Node/runtime, browsers, build scripts |
| Backend / APIs | Service design, identity, data, API contracts | Add test/lint/type-check commands | Database, secrets, test services |
| Mobile Android | Lifecycle, permissions, signing, release safety | Android workflow template / dedicated runner | SDK, JDK, signing, emulator/device matrix |
| Mobile iOS | Privacy, entitlements, signing, lifecycle | macOS runner and Xcode workflow | Apple developer identity, Xcode version, signing |
| Desktop / CLI | Packaging, updates, filesystem and OS security | Native OS build matrix | Target OS, compilers, installers |
| Data / ML / AI | Data lineage, evaluation, model and tool safety | Explicit data/model checks | Datasets, GPUs, providers, privacy rules |
| Cloud / DevOps / SRE | IaC, IAM, observability, recovery | Lint/plan/scan workflows | Cloud account, remote state, environment approvals |
| Enterprise / SaaS | Governance, compatibility, tenant isolation | Profile-driven CI + optional scanners | Identity, audit requirements, availability targets |
| Commerce / finance | Transaction integrity and high-impact approvals | Domain-specific integration tests | Provider sandbox, reconciliation, legal controls |
| Game / graphics | Asset pipeline, performance, platform packaging | Engine-specific workflow | Unity/Unreal/Godot toolchain, runners, licensing |
| IoT / embedded | Firmware integrity, hardware boundaries, reproducible builds | Cross-compiler/toolchain workflow | Target boards, hardware test fixtures, signing |
| C/C++ / native | Memory safety, compiler flags, dependency provenance | CMake/Make/Bazel workflow | Compiler and platform matrix |

## Configuration principles

- Detect and document what actually exists; do not enable every ecosystem by default.
- Keep test, build, packaging, signing, deployment, and publishing commands separate.
- Use specialized runners for workloads that Linux-hosted defaults cannot perform.
- Require hardware, cloud, GPU, or paid-provider access only when the real project needs it.
- Record untested platforms and environment-dependent limitations honestly.
