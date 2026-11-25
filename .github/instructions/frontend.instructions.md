---
applyTo: "**/*.{html,htm,css,scss,sass,less,js,jsx,ts,tsx,vue,svelte,astro}"
---

# Frontend engineering instructions

- Inspect the current design system, routing, component conventions, rendering model, state management, and browser support before changing UI code.
- Preserve established product identity and interaction patterns unless a redesign is explicitly requested.
- Use semantic HTML, accessible names, keyboard support, visible focus, suitable contrast, and responsive layouts.
- Handle loading, empty, error, offline, permission, retry, and success states explicitly.
- Keep authorization and authoritative business state on trusted server-side boundaries.
- Validate untrusted input and encode output for its rendering context.
- Avoid unnecessary dependencies, duplicated state, expensive rendering, layout shifts, and oversized bundles.
- Respect reduced motion, localization, time zones, and platform-specific behavior.
- Add/update tests with the repository's actual test framework and inspect affected views where tooling exists.
