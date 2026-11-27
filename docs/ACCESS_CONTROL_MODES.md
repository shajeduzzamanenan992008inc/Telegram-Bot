# Global Private / Public Access Modes

## Overview

Enan Ultra supports a database-persisted **global access mode**. The mode is
authoritative for ordinary users; local `ACCESS_MODE` configuration can never
override it.

## Mode Behavior

### Private Mode (`private`)

- Only trusted administrators (`ADMIN_ID` / `ADMIN_USER_IDS`) may access
  protected functionality.
- Every ordinary user is denied, including users listed in `ALLOWED_USER_IDS`.
- Existing sessions, Mini App `initData`, and callback queries are re-checked on
  every request; nothing is exempt.
- The denial reason is `private_mode`.

### Public Mode (`public`)

- Ordinary users are authorized according to the existing policy:
  - `ACCESS_MODE=public` permits any non-blocked user.
  - `ACCESS_MODE=allowlist` permits only `ALLOWED_USER_IDS`.
- Blocked users remain denied; rate limits, quotas, and admin-only commands are
  unchanged.
- `ACCESS_MODE` is therefore a **policy filter inside Public Mode**, not a
  switch that can bypass Private Mode.

### Default Mode

- The default persisted mode is `private`.
- It is stored in the `global_access_mode` singleton row (migration v17).
- A missing row is treated as `private`; `set_global_access_mode()` upserts, so a
  damaged row cannot silently turn a mode change into a no-op.

## Authorization Order

`AccessControlService.is_access_allowed(user_id)` evaluates:

1. Trusted administrator → `(True, "admin")`.
2. Read the database mode. If the read fails or the stored value is not
   `private`/`public` → `(False, "mode_unavailable")` (fail closed).
3. Blocked user → `(False, "blocked")`.
4. `private` → `(False, "private_mode")`.
5. `public` → apply `is_authorized()` (admin, public policy, allowlist) →
   `(True, "allowed")` or `(False, "unauthorized")`.

`require_access()` raises `AccessDeniedError` with the same reason.
`enforce_private_mode_for_update()` sends the user-facing message and returns
`False`.

## Administration

Only administrators may change the global mode:

```
/setmode private
/setmode public
```

- `/setmode` is a safe dispatcher: `private|public` selects the global mode
  (admin-only); chat styles (`default|sarcastic|anime|professional|regional`)
  select the per-chat assistant mode. The former duplicate command mapping that
  let one registration shadow the other has been removed.
- Successful changes are written to `audit_events`
  (`global_access_mode.change`).
- Rejected non-admin attempts are **not** currently written to the audit log;
  they receive the admin-only refusal message.

## Enforcement Points (verified)

| Entry point | Enforcement |
| --- | --- |
| Telegram commands and messages | `BotHandlers._gate()` → `enforce_private_mode_for_update()` |
| Menu callbacks | `menu_callback()` → `is_access_allowed()` |
| Mini App API | `MiniAppServer._authenticate()` → `is_access_allowed()` |
| Mode change | `_gate(update, admin=True)` then `require_access()` |
| Data-rights commands (`/privacy`) | `_gate(update, allow_blocked=True)` deliberately skips ordinary-user gating so blocked users can export/delete their own data |
| Background tasks | No user-facing authorization decision is made; see limitations |

## Failure Behavior

- A database error while reading the mode denies ordinary users with
  `mode_unavailable`; administrators keep recovery access.
- A malformed/unrecognized stored mode is treated the same way.
- No in-memory mode cache exists, so another process's change is visible on the
  next check.

## Known Limitations

- Private Mode is global; `ALLOWED_USER_IDS` does not grant access in PRIVATE.
- Administrators are always allowed, including to fix the mode; blocking an
  administrator does not override that.
- Background jobs that do not call `AccessControlService` inherit no mode check
  of their own.
