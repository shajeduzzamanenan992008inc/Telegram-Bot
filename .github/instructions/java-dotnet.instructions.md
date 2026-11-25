---
applyTo: "**/*.{java,kt,kts,cs,fs,vb},**/pom.xml,**/build.gradle,**/build.gradle.kts,**/*.csproj,**/*.fsproj,**/*.sln,**/*.slnx"
---

# JVM and .NET engineering instructions

- Identify the runtime/SDK, framework, build tool, target platforms, module layout, and supported versions from repository files.
- Preserve dependency injection, transaction, configuration, and error-handling conventions already used by the application.
- Keep public interfaces and serialized contracts backward-compatible when required.
- Use cancellation and timeouts for I/O, dispose resources correctly, and avoid unbounded thread/task creation.
- Validate and authorize at service boundaries; do not expose secrets or internal details in logs.
- Run the repository's formatter, static analysis, unit/integration tests, and release build for the supported target framework.
