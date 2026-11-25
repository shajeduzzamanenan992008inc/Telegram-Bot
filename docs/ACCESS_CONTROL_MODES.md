# Global Private / Public Access Modes

## Overview

The Enan Ultra Telegram Bot supports a **Global Private Mode** / **Public Mode** access control system. This system determines who can use protected bot functionality at the backend level.

## Mode Behavior

### Private Mode (`private`)

When Private Mode is enabled:

- **Only authenticated and authorized administrators** may access protected bot features.
- **All ordinary users** are denied access to bot functionality.
- **Existing user sessions** do not bypass the restriction.
- **Previously issued API tokens** do not bypass the restriction.
- **Telegram commands, callbacks, Mini App requests, and API endpoints** all enforce the same restriction.
- **User-facing responses** clearly indicate that the service is currently restricted.
- **Administrative recovery and mode-switching** functionality remains available to authorized administrators.

### Public Mode (`public`)

When Public Mode is enabled:

- **Ordinary users** may use the bot according to existing authentication, authorization, rate-limit, and access policies.
- **Existing security checks** remain active (blocked users, rate limits, etc.).
- **Admin-only commands and functions** remain admin-only.
- **Rate limits, authentication, validation, and abuse protection** remain enabled.

### Default Mode

- The **default global mode is `private`**.
- Mode is **persisted in the database** and survives process restart.
- Mode changes are **atomic** and observable by all workers/instances.

## Administration

### Changing the Mode

Only verified administrators may change the global mode:

```
# Switch to Private Mode (only admins can use the bot)
/setmode private

# Switch to Public Mode (ordinary users restored)
/setmode public
```

- **Confirmation** is required before switching modes.
- Mode changes are **audited** with administrator identity, timestamp, and outcome.
- Attempts by non-administrators are **rejected** and logged.

### Administrator Protection

- Never trust an admin ID, role, or permission supplied by an ordinary client.
- Administrator identity is **validated using the existing trusted authentication mechanism**.
- **Unauthorized mode-change requests** are rejected and audited.
- **Privilege escalation** through Telegram callbacks, API requests, or Mini App parameters is prevented.

## Database Persistence

- The global mode is stored in the `global_access_mode` table.
- Schema migration `v17` creates the table with safe defaults.
- **Default mode**: `private`.
- Mode survives **process restart** (persisted in SQLite).
- **Atomic updates** via single-document `UPDATE ... WHERE id=1`.
- Correct behavior under **concurrent requests** (SQLite WAL mode handles this).

## API Behavior

### Access Control Service

The `AccessControlService` provides centralized mode enforcement:

- `is_private_mode()`: Check if global mode is private.
- `is_access_allowed(user_id)`: Returns `(allowed, reason)` tuple.
- `require_access(user_id)`: Raises `AccessDeniedError` if not allowed.
- `enforce_private_mode_for_update(update)`: Enforces mode for Telegram updates.

### Access Denial Messages

When access is denied in Private Mode, users see:

```
🔒 The bot is currently in **Private Mode**.
Only administrators can use this feature at this time.
```

When access is denied for other reasons:

```
Your access to this bot has been restricted.
```

or

```
You are not authorized to use this bot.
```

## Security Considerations

- **Unauthorized mode switching** is prevented by admin verification.
- **Stale session access** is prevented — sessions do not bypass mode restrictions.
- **Stale API token access** is prevented — tokens do not bypass mode restrictions.
- **Callback authorization bypass** is prevented — all callback handlers enforce mode.
- **Mini App authorization bypass** is prevented — Mini App endpoints enforce mode.
- **Direct API access bypassing the UI** is protected by centralized service.
- **Race conditions during mode transitions** are handled by SQLite's atomic operations.
- **Cache inconsistency** is avoided — mode is read from database on each check.
- **Forged Telegram identity or client-supplied role** is prevented by identity verification.
- **CSRF risks** are mitigated by loopback-only dashboard and origin checks.

## Audit Logging

Every mode-change attempt is logged in the `audit_events` table with:

- Previous mode
- Requested mode
- Result (success/failure)
- Trusted administrator identity (if verified)
- Timestamp
- Safe request metadata (no secrets, tokens, or session cookies)

Audit logs are **not modifiable by ordinary users**.

## Known Limitations

- Mode changes require administrator privileges.
- Private Mode blocks **all** ordinary users — there is no whitelist/blacklist override within Private Mode itself (use Allowlist mode `access_mode=allowlist` for finer control).
- Mode is global — there is no per-chat or per-user mode configuration at the global level.