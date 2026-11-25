# Private/Public Mode Security

This document describes the access-control behavior verified in the source and
tests as of 2026-11-25.

## Critical defect fixed

`AccessControlService.is_access_allowed()` previously returned `"allowed"` when
`settings.access_mode == "public"` **before** reading the database mode. With the
shipped default `ACCESS_MODE=public`, a database PRIVATE mode could be bypassed
for ordinary users.

A second defect was found during remediation: migration v17 inserted the
`global_access_mode` row with an unbound `?` placeholder, leaving the table empty
and making `UPDATE ... WHERE id=1` affect no rows. `/setmode public` therefore
did nothing. Both are fixed:

- The database mode is read before any ordinary-user authorization decision.
- `mode_unavailable`/unknown values fail closed.
- Migration seeding uses `CURRENT_TIMESTAMP`; `set_global_access_mode()` upserts.

## Current decision order

1. Administrator → allow (`admin`).
2. Read authoritative database mode; read error or invalid value → deny
   (`mode_unavailable`).
3. Blocked user → deny (`blocked`).
4. Database mode `private` → deny (`private_mode`).
5. Database mode `public` → existing policy (`is_authorized`);
   `ACCESS_MODE=public` or allowlisted users pass, everyone else is
   `unauthorized`.

Consequences:

- Local `ACCESS_MODE` cannot restore access while the database says PRIVATE.
- PRIVATE denies allowlisted ordinary users too.
- PUBLIC with `ACCESS_MODE=allowlist` still denies unknown users.

## Mode switching

- `/setmode private|public` is admin-only (`_gate(update, admin=True)` plus an
  explicit `is_admin` check and `require_access`).
- The `/setmode` registration collision was removed. One dispatcher now handles
  global modes and the per-chat assistant modes.
- Successful changes are audited twice by design: the database method writes
  `global_access_mode.change`, and the handler writes a second audit event with
  the requesting administrator.
- Rejected non-admin attempts are not written to the audit log.

## Entry points

| Surface | Behavior |
| --- | --- |
| Commands/messages | `_gate()` enforces mode before handler logic; denial is replied to the user |
| Callback queries (`main:help/export/cancel`) | `menu_callback()` now calls `is_access_allowed()`; previously it only used `is_authorized()` and bypassed the database mode |
| Mini App | `_authenticate()` validates signed `initData`, then calls `is_access_allowed()`; every API call is re-authorized, so earlier sessions/tokens do not bypass a switch |
| `/privacy` for blocked users | intentionally exempt from ordinary-user gating (`allow_blocked=True`) so data-rights exports/deletions remain possible |
| Admin ZIP update callbacks | admin- and chat-scoped checks; not ordinary-user reachable |

## Concurrency and persistence

- SQLite WAL mode with a 5-second busy timeout.
- Each authorization reads the mode; there is no process-local cache.
- Concurrent changes are serialized by SQLite; the final value is always one of
  `private`/`public`, never a partial state.

## Audit log

Successful global mode changes record actor, action, target mode, outcome, and
the hash-chain fields (`previous_hash`, `event_hash`). Chain verification is
available through `verify_audit_chain()`.

## Residual risks

- An administrator is always allowed, even if also present in `blocked_users`.
- Background jobs that do not call the access-control service have no mode check
  of their own.
- The admin dashboard is loopback-only and is not part of the ordinary-user mode
  path.
- CI workflow behavior is unverified until the workflows run on GitHub.
