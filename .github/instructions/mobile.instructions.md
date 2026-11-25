---
applyTo: "**/android/**,**/ios/**,**/*.xcodeproj/**,**/*.xcworkspace/**,**/*.{swift,kt,kts,dart}"
---

# Mobile engineering instructions

- Confirm supported OS/device versions, architecture, build variants, signing flow, permissions, and release process before changing platform code.
- Keep signing keys and store credentials out of source code, logs, and pull-request contexts.
- Request only necessary permissions and explain sensitive access to users.
- Handle offline mode, app lifecycle, deep links, notifications, background execution, accessibility, and privacy deliberately.
- Test on representative emulators/simulators and real devices where available; do not claim device coverage that was not performed.
- Separate debug, staging, and production endpoints and signing configurations.
- Make upgrades reversible and review data migration, store policy, and backward compatibility.
