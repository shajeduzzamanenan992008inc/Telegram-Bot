---
applyTo: "**/*.{js,jsx,cjs,mjs,ts,tsx,cts,mts},**/package.json,**/*lock*"
---

# JavaScript and TypeScript engineering instructions

- Identify the package manager from the committed lockfile and use its reproducible install command; do not regenerate lockfiles casually.
- Inspect runtime/module format, TypeScript configuration, build scripts, lint/test scripts, framework conventions, and supported Node versions.
- Prefer strict types and explicit validation at trust boundaries; do not use `any` to silence unresolved design problems.
- Handle rejected promises, cancellation, timers, streams, and event listener lifetimes intentionally.
- Keep server-only secrets and privileged code out of browser bundles.
- Review dependency lifecycle, bundle impact, source maps, environment variables, and production build behavior.
- Run the project's defined formatting, lint, type-check, test, and build commands.
