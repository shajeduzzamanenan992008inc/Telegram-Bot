---
applyTo: "**/*.py,**/pyproject.toml,**/requirements*.txt,**/Pipfile*,**/uv.lock,**/poetry.lock"
---

# Python engineering instructions

- Read the existing package layout, import boundaries, supported Python versions, dependency manager, type-checker, formatter, and test framework before editing.
- Preserve the project's public API and packaging conventions; avoid adding parallel app factories or service layers.
- Use type hints for public interfaces, context managers for resource lifetimes, and explicit exception handling.
- Validate untrusted data at boundaries; use parameterized queries and safe serialization.
- Keep async/sync boundaries explicit and avoid blocking I/O inside async event loops.
- Keep dependency versions and lockfiles aligned with the project's chosen tooling.
- Add focused tests and run the repository's actual format, lint, type-check, and test commands.
