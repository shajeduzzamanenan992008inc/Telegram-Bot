# HTTP API reference

This document describes routes implemented by `src/enan_ultra/mini_app.py` and
`src/enan_ultra/dashboard.py`. Both are separate FastAPI applications; neither
publishes an OpenAPI document. This is a source-based reference, not evidence of
a live deployment or remote integration test.

## Telegram Mini App

The Mini App is available only when configured with a public HTTPS root URL.
The ASGI listener is configured separately and must stay on loopback behind a
TLS reverse proxy. Every request, including `/` and `/healthz`, must arrive with
the configured `Host` and `X-Forwarded-Proto: https`. A supplied `Origin` must
match the configured origin. Responses from the application include `no-store`
and browser security headers.

Except for static assets, the JSON API requires:

```http
Authorization: tma <Telegram.WebApp.initData>
```

The server validates Telegram's HMAC signature and signed `auth_date` (maximum
age 12 hours, at most 30 seconds future skew), rejects duplicate fields, and
uses the user ID from the signed payload. The account must also pass the
configured access policy and not be blocked. Client-provided user IDs are not
used for identity or ownership. Requests are limited to 64 KiB; the per-user
API rate limit is 120 requests per rolling minute.

| Method and path | Authentication | Request | Success response / behavior | Explicit errors |
| --- | --- | --- | --- | --- |
| `GET /healthz` | None | None | `{"status":"ok"}`. Process-level liveness only; it does not check database/provider readiness. | `421` wrong host/proxy scheme; `403` mismatched supplied Origin. |
| `GET /` | None | None | Mini App HTML (`text/html`). | Same middleware errors as above. |
| `GET /assets/{path}` | None | Static path under the bundled web app assets directory. | Static asset response. | Static-file errors are handled by Starlette; exact status/body is NOT VERIFIED here. |
| `GET /api/v1/bootstrap` | Signed Telegram `initData` | None | `user` (first and last name), `themes`, `state`, bounded `history`, and `telegram.color_scheme`. `history` is empty in temporary-chat mode. | Common auth errors below. |
| `GET /api/v1/themes` | Signed Telegram `initData` | None | `{"themes":[...]}` from the client theme registry. | Common auth errors below. |
| `POST /api/v1/theme` | Signed Telegram `initData` | JSON action: `mode` (`mode`, optional `theme_id`), `setting` (`key`, `value`), `favorite` (`theme_id`), or `reset`. | Usually `{"state":...}`; `favorite` returns `{"state":...,"favorited":boolean}`. State includes mode/topic/theme IDs, confidence/reason, labels/icon, learning/tracking and appearance settings, palette, favorites. | `400` malformed JSON/object or invalid/unknown operation/setting. Common auth errors. |
| `GET /api/v1/chat/history` | Signed Telegram `initData` | None | `{"history":[{"role":"user\|assistant","content":"..."}]}`; bounded by configured history and input budget; empty during temporary chat. | Common auth errors below. |
| `GET /api/v1/tasks` | Signed Telegram `initData` | None | `{"tasks":[...]}`. Each task includes ID, text, status, priority, deadline, timestamps, prerequisites (`depends_on`), blockers (`blocked_by`) and `blocked_count`; maximum 100. | Common auth errors below. |
| `POST /api/v1/tasks` | Signed Telegram `initData` | JSON action `add` (`text`, optional `priority`, `due_at`, integer-array `depends_on`), `done` (`task_id`), or `delete` (`task_id`). | `{"task_id":id,"tasks":[...]}`. Every DB operation is scoped to the signed user; prerequisite completion and deletion rules are enforced server-side. | `400` invalid request/action/dependency; `404` task not found/open task missing; `409` prerequisite unfinished or open dependent task prevents deletion. Common auth errors. |
| `POST /api/v1/chat` | Signed Telegram `initData` | JSON `{"text":"..."}` | Server-sent events (`text/event-stream`): `theme` carries topic/theme decision; each `token` carries `{"text":...}`; `done` carries final `text`, `model`, `theme_id`, `topic_id`; generation failure emits an `error` event with a generic message. Non-temporary chats save bounded history; temporary chats do not save it. | `400` missing/blank text or invalid JSON; `413` body/input too large; `403` unauthorized/blocked; `429` concurrent request, cooldown, rate or budget limit; `401` invalid/missing/expired signed session; `503` no configured model. Provider failure after streaming starts is an SSE `error`, not necessarily a new HTTP status. |

Common Mini App middleware/auth errors use JSON `{"detail":"..."}`:

| Status | Meaning |
| --- | --- |
| `400` | Invalid content length, JSON, or JSON object. |
| `401` | Missing, invalid, or expired Telegram signed session. |
| `403` | Supplied Origin mismatch, account not authorized, or account blocked. |
| `413` | Request body over 64 KiB, or an endpoint-specific input limit exceeded. |
| `421` | Host or forwarded HTTPS scheme does not match configured Mini App URL. |
| `429` | More than 120 API requests per user/minute, chat cooldown/concurrency, or usage limit. |
| `503` | No model configured for chat. |

### Mini App test coverage

- `tests/test_runtime_and_servers.py::test_dashboard_and_mini_app_use_the_shared_server_builder` covers shared server construction for the dashboard and the Mini App.
- `tests/test_webapp_theme_ui.cjs` covers the theme schema, contrast, accessibility and element-contract checks the frontend relies on.
- Init-data signature/timestamp validation, Origin/Host rejection, per-user separation, task ownership and the SSE chat flow are implemented in `src/enan_ultra/mini_app.py` but are not exercised by an automated test in this checkout. Live Telegram, reverse-proxy and AI-provider integration are not established here either.

## Admin dashboard

The dashboard is a separate loopback-only ASGI application. Non-loopback host
requests receive `403`. It is intended for local access, normally through an
SSH tunnel; it must not be exposed directly to the Internet. Application
responses use `no-store` and browser security headers.

`POST /auth/login` accepts JSON `{"token":"<dashboard access key>"}` and requires
a same-origin `Origin`. On success it returns `{"ok":true}` and sets the
`enan_admin` HttpOnly, SameSite=Strict cookie, signed with the configured
dashboard key and valid for 12 hours. The cookie has `secure=False` in the
current source because the dashboard is loopback-only; do not place it on a
public HTTP listener. Login attempts are limited to five per peer in five
minutes. Logout requires a same-origin Origin and deletes the cookie.

All `/api/v1/*` dashboard routes require a valid `enan_admin` cookie. Every
state-changing dashboard route also requires same-origin `Origin` matching
request scheme and host. Unauthenticated API calls return `401`; missing or
cross-origin mutation Origin returns `403`.

| Method and path | Request | Success response / behavior | Explicit errors and evidence |
| --- | --- | --- | --- |
| `POST /auth/login` | JSON `token` | `{"ok":true}` plus 12-hour cookie. | `401` invalid access key; `403` missing/mismatched Origin; `429` login-attempt limit. Branches are implemented in `dashboard.py`. |
| `POST /auth/logout` | No body | `{"ok":true}` and cookie deletion. | `403` missing/mismatched Origin. Implemented in `dashboard.py`. |
| `GET /` | None | Dashboard HTML. | Loopback host restriction. Auth not required to return the page; API data remains protected. |
| `GET /api/v1/status` | None | Object: `state`, `uptime_seconds`, `dashboard`, `active_live_sessions`, `resources`. | `401` without valid cookie; the payload reports `dashboard=loopback`. |
| `GET /api/v1/health` | None | Health snapshot: `status` (`ok` or `degraded`) and resource metrics. | `401` without valid cookie; the payload carries resource and disk metrics. |
| `GET /api/v1/providers` | None | Provider status returned by the router. Exact provider object fields are NOT VERIFIED in this route's tests. | `401` without valid cookie. Other provider failures are NOT VERIFIED. |
| `GET /api/v1/usage` | None | Counts and cost: `requests_today`, `cost_today_usd`, `pending_approvals`, `running_agents`, `feedback_positive_today`, `feedback_negative_today`. | `401` without valid cookie. |
| `GET /api/v1/users` | None | List of private-chat `user_id`, `last_active`, and `message_count`; no message content. | `401` without valid cookie. |
| `POST /api/v1/users/{user_id}/clear-memory` | Positive integer path ID; same-origin Origin. No body required. | `{"ok":true}`; clears that ID's private history and long-term memory, not shared group history; records an audit event. | `400` non-positive ID; `401` unauthenticated; `403` missing/cross-origin Origin. Success, invalid ID and Origin enforcement are implemented in `dashboard.py`. |
| `GET /api/v1/feedback` | None | List of aggregated recent negative-feedback category suggestions (`category`, `negative_count`, `suggestion`). | `401` without valid cookie. |
| `GET /api/v1/jobs` | None | Up to 60 latest agent-job records with ID, user/task/provider/status, steps, cost, summary and timestamps. | `401` without valid cookie. |
| `GET /api/v1/approvals` | None | Up to 100 pending, unexpired approvals with ID, requester, kind, summary, required approver, status and timestamps. | `401` without valid cookie. |
| `POST /api/v1/approvals/{approval_id}/approve` | Integer path ID; same-origin Origin. | `{"ok":true,"message":"..."}` if the approval action succeeds. | `401` unauthenticated; `403` missing/cross-origin Origin; `409` approval action rejected. Unauthorized approval is tested; successful approval and conflict behavior are NOT VERIFIED by dashboard route tests. |
| `POST /api/v1/approvals/{approval_id}/deny` | Integer path ID; same-origin Origin. | `{"ok":true}` if denial succeeds. | `401`, `403`, or `409` on corresponding conditions. Route success is NOT VERIFIED by dashboard route tests. |
| `GET /api/v1/vm` | None | Merges VM-control status with `logs` (up to 20 sanitized lines). Exact status object fields are NOT VERIFIED here. | `401` without valid cookie; backend failure response is NOT VERIFIED. |
| `POST /api/v1/vm/restart` | No body; same-origin Origin. | Creates an admin approval request; returns `{"ok":true,"approval_id":id}`. It does not directly restart the service. | `401` unauthenticated; `403` missing/cross-origin Origin. Route success is NOT VERIFIED by dashboard route tests. |
| `GET /api/v1/home-assistant` | None | Configured entity state list; returns `[]` when integration is absent or fails. | `401` without valid cookie. Exact entity fields are NOT VERIFIED here. |
| `POST /api/v1/voice/transcribe` | Raw audio body; optional `Content-Type`; same-origin Origin. | `{"text":"..."}` with output truncated to 4,000 characters. | `401`, `403`, `404` voice disabled, `400` empty body, `413` over min(configured limit, 8 MiB), `429` usage limit, `503` transcription provider unavailable. Not verified by dashboard route tests. |
| `GET /api/v1/audit` | None | Up to 100 newest audit rows: actor, action, target, outcome, timestamp. | `401` without valid cookie. |
| `GET /api/v1/events` | None | SSE `telemetry` event every 8 seconds containing `metrics` (usage counters) and `providers` status; stream ends on disconnect or invalidated session. | `401` without valid cookie at connection. Stream behavior is NOT VERIFIED by dashboard route tests. |

Dashboard JSON error bodies for explicit `HTTPException` branches use
`{"detail":"..."}`. Framework-generated parsing/validation failures and
unhandled dependency errors are not exhaustively specified here.

### Dashboard test coverage

`tests/test_runtime_and_servers.py::test_dashboard_and_mini_app_use_the_shared_server_builder`
covers only shared server construction. Login, session cookies, same-origin
enforcement and the individual `/api/v1/*` routes are implemented in
`src/enan_ultra/dashboard.py` but are not exercised by an automated test in this
checkout, and nothing here proves real loopback networking, cookie handling or
deployment behavior.

## Scope and deployment note

These routes are derived from the current local source. HTTP request/response
behavior has only the test coverage explicitly listed above. No live Telegram,
production dashboard, reverse proxy, provider, voice service, or deployment
verification is claimed.