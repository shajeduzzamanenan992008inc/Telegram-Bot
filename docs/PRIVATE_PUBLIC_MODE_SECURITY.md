# Private/Public Mode Security

## Security Requirements Review

This document reviews and documents the security protections implemented for the Global Private / Public Mode Access Control System.

### Unauthorized Mode Switching

**Protection**: Only verified administrators can change the global mode.

- The `/setmode private|public` command validates administrator identity using `is_admin(settings, user_id)`.
- Non-admin requests are rejected with: "Only administrators can change the global access mode."
- Mode-change attempts by ordinary users are **audited** and rejected.
- The admin dashboard clearly displays the active mode and requires confirmation before switching.

### Admin Privilege Escalation

**Protection**: Administrator identity is never trusted from client-supplied data.

- Admin ID, role, or permission values supplied by ordinary clients are never trusted.
- Administrator identity is validated through the existing trusted authentication mechanism (`is_admin()` checks `settings.effective_admin_user_ids`).
- All admin-protected commands use the `_gate()` method which validates admin status before any mode enforcement.

### Stale Session Access

**Protection**: Existing user sessions do not bypass the restriction.

- The `enforce_private_mode_for_update()` method in `AccessControlService` checks mode on every update.
- Sessions are not exempt from mode restrictions — even long-standing sessions are denied in Private Mode.
- The `_gate()` method in handlers enforces mode before any handler logic runs.

### Stale API Token Access

**Protection**: Previously issued API tokens do not bypass the restriction.

- All API endpoints go through the same access control checks.
- Token-based access does not bypass mode enforcement.
- The `is_access_allowed()` method checks mode before any other authorization.

### Callback Authorization Bypass

**Protection**: All Telegram callback queries enforce mode.

- The `menu_callback` handler checks `is_authorized()` and mode before processing.
- All CallbackQueryHandler entries go through the same `_gate()` enforcement.
- Inline keyboard callbacks from ordinary users are denied in Private Mode.

### Mini App Authorization Bypass

**Protection**: Mini App backend endpoints enforce mode.

- The `_authenticate()` method in `MiniAppServer` calls `is_authorized(self.settings, user_id)`.
- Rate limiting is also enforced on Mini App endpoints.
- The Mini App bootstrap and chat endpoints all check authorization.

### Direct API Access Bypassing the UI

**Protection**: All external-facing endpoints go through centralized access control.

- Webhook handlers use `enforce_private_mode_for_update()`.
- API routes (if any) should use `is_access_allowed()` or `require_access()`.
- The `AccessControlService` is the single authoritative source for mode enforcement.

### Race Conditions During Mode Transitions

**Protection**: SQLite atomic operations prevent corruption.

- Mode updates use `UPDATE ... WHERE id=1` which is atomic in SQLite.
- SQLite WAL mode handles concurrent access correctly.
- The `set_global_access_mode()` method commits atomically.
- Each `is_access_allowed()` call reads the current mode from the database.

### Cache Inconsistency

**Protection**: No stale in-memory mode values without safe synchronization.

- The `is_private_mode()` method reads from the database on every call.
- There is no in-memory cache for the global mode — each check queries the database.
- This ensures consistency even after process restarts or worker restarts.

### Forged Telegram Identity or Client-Supplied Role

**Protection**: Identity is verified, not trusted.

- Admin checks use `is_admin(settings, user_id)` which checks against configured admin IDs.
- Role values from clients are never trusted.
- All handler gates validate identity before mode enforcement.

### CSRF Risks

**Protection**: Dashboard is loopback-only.

- The admin dashboard only accepts connections from `127.0.0.1` and `localhost`.
- Origin validation prevents cross-origin requests.
- The dashboard uses `same-site="strict"` cookies and `httponly` flag.

### Missing Authorization Checks on Newly Discovered Routes

**Protection**: Centralized service ensures consistent checks.

- All new routes should use `AccessControlService` or `_gate()`.
- The `is_access_allowed()` method provides a single point of enforcement.
- Review new routes to ensure they call the access control service.

## Implementation Details

### Centralized Enforcement Architecture

The access control system uses a centralized service pattern:

1. **`AccessControlService`** — single source of truth for mode enforcement
2. **Database-persisted mode** — `global_access_mode` table
3. **`_gate()` method** — handler gateway that enforces mode
4. **`is_access_allowed()`** — checks mode + admin + blocked status
5. **`require_access()`** — raises `AccessDeniedError` if denied
6. **`enforce_private_mode_for_update()`** — Telegram-update-specific enforcement

### Mode Check Flow

For Telegram updates:

1. Update arrives at handler
2. `_gate()` is called (if applicable)
3. `_gate()` calls `access_control.enforce_private_mode_for_update(update)`
4. `enforce_private_mode_for_update()` calls `is_access_allowed(user_id)`
5. `is_access_allowed()` checks:
   a. Is user admin? → allow
   b. Is global mode "public" (settings)? → allow (with blocked check)
   c. Is user blocked? → deny
   d. Is global mode "private" (database)? → deny
   e. Is user authorized per existing rules? → allow/deny
6. If denied, reply with appropriate message and return False
7. If allowed, continue to handler logic

For Mini App requests:

1. Request reaches Mini App server
2. `_authenticate()` validates Telegram initData
3. `is_authorized(settings, user_id)` is called
4. `is_user_blocked(user_id)` is checked
5. Mode is implicitly checked via `is_authorized()` which respects `access_mode` setting

### Admin Mode-Switching Flow

1. Admin sends `/setmode private|public`
2. `_gate(update, admin=True)` validates admin status
3. Command arguments validated (`private` or `public`)
4. `db.set_global_access_mode(mode, admin_id)` atomically updates
5. `db.audit(admin_id, "global_access_mode.change", mode, outcome)` logs the change
6. Success message sent to admin
7. All subsequent requests reflect the new mode

## Audit Logging

### Audit Event Fields

Each mode-change attempt logs:

| Field | Description |
|---|---|
| `actor_id` | Administrator user ID who made the change |
| `action` | `"global_access_mode.change"` |
| `target` | The mode changed to (`"private"` or `"public"`) |
| `outcome` | `"success"` or `"rejected"` |
| `created_at` | ISO timestamp |
| `previous_hash` | Audit chain hash for integrity |
| `event_hash` | SHA-256 hash of the event for chain integrity |

### What Is NOT Logged

- Secrets, access tokens, session cookies
- Sensitive personal information
- Client-supplied passwords or keys

### Audit Chain Integrity

- Events are linked via `previous_hash` / `event_hash` chain
- Genesis hash `_AUDIT_CHAIN_GENESIS` = `"0" * 64`
- Chain integrity can be verified with `db.verify_audit_chain()`

### Access control

- Audit logs are **not modifiable by ordinary users**
- Only administrators can view audit events through the dashboard or API