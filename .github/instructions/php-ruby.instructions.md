---
applyTo: "**/*.{php,rb},**/composer.json,**/composer.lock,**/Gemfile,**/Gemfile.lock"
---

# PHP and Ruby engineering instructions

- Preserve framework conventions, supported runtime versions, autoloading, dependency constraints, and current test/lint tools.
- Validate external input, enforce authorization on every protected operation, and use parameterized persistence APIs.
- Handle errors and transaction boundaries explicitly; do not leak stack traces or secrets to clients.
- Keep dependency lockfiles in sync and review plugins/extensions as part of the software supply chain.
- Add regression tests and run the project's actual static-analysis, style, test, and build/package commands.
