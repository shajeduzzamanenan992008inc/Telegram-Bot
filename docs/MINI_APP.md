# Adaptive theme engine and Telegram Mini App

For short task-focused entry points, see [Mini App setup](./MINI_APP_SETUP.md), [adaptive theme engine](./ADAPTIVE_THEME_ENGINE.md), and [accessibility guidance](./PERFORMANCE_AND_ACCESSIBILITY.md). This page remains the canonical deployment/API reference.

The optional OMEGA workspace combines per-user topic themes with a small Telegram Mini App for chat and visual settings. It is separate from the authenticated admin dashboard. The app is off until `MINI_APP_URL` is set.

## Enable it behind HTTPS

The app must use a public HTTPS origin without a URL path, query, or fragment. The ASGI server stays bound to loopback; put a TLS reverse proxy in front of it and do not expose port 8788 to the Internet.

Set these values in the service environment:

```dotenv
MINI_APP_URL=https://omega.example.com
MINI_APP_HOST=127.0.0.1
MINI_APP_PORT=8788
```

Example Caddy site on the same VM:

```caddyfile
omega.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8788
}
```

Caddy obtains and renews the public TLS certificate and forwards the host and HTTPS scheme. For another proxy, preserve the original `Host` and send `X-Forwarded-Proto: https`. The app checks both values and, when present, requires the request `Origin` to match `MINI_APP_URL`. Keep the admin dashboard on its own loopback port and SSH tunnel; it does not share Mini App authorization.

After editing the environment, restart the bot. It starts the ASGI server, sets the Telegram chat menu button, and registers `/app`. `/start` also displays a web app button. Open `/app` in a private Telegram chat to check it. If no public HTTPS origin is configured, `/app` explains how to enable it and the server is not started.

## Identity and API

The browser sends Telegram's signed `Telegram.WebApp.initData` in `Authorization: tma <initData>` on API requests. The backend validates the Telegram HMAC signature using the bot token, rejects duplicate/malformed fields, checks the signed `auth_date` is no more than 12 hours old (with a small future clock allowance), and takes the user ID from that verified payload. `initDataUnsafe`, client-supplied user IDs, and cookies are not used as authentication.

The app checks the existing allowlist/public access policy and blocked-user state on every request. Per-user request throttling, chat cooldown, local/cloud privacy mode, memory and temporary-chat settings, existing history, provider routing, daily/monthly budget reservations and cost accounting apply to app chat too. Chat history and visual settings use the verified Telegram user ID. A browser disconnect cancels the provider stream and releases the per-user generation lock.

The current JSON endpoints are:

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/bootstrap` | Authenticated theme catalog, saved visual preferences and bounded chat history |
| `GET /api/v1/themes` | Authenticated theme catalog |
| `POST /api/v1/theme` | Set mode/visual setting, toggle a favorite, or reset visual preferences |
| `GET /api/v1/chat/history` | Read the authenticated user's recent history |
| `POST /api/v1/chat` | Send a prompt and receive server-sent `theme`, `token`, `done`, or `error` events |
| `GET /api/v1/tasks` | Read the authenticated user's personal task list and prerequisite state |
| `POST /api/v1/tasks` | Add a task, mark it done, or delete it; ownership and prerequisites are enforced server-side |
| `GET /healthz` | Minimal process health response for the proxy/operator |

Authenticated API responses are marked `no-store`; security headers and a restrictive Content Security Policy are applied. The UI loads static files from the same origin. It does not provide an admin API or cross-user history access.

## Topic and visual preferences

`services/theme_engine.py` reads JSON definitions in `src/enan_ultra/webapp/themes/`. Each definition includes a stable ID, Bengali/English labels, icon, palette, motion hints and bilingual detector keywords. The registry rejects unsafe IDs and color values, malformed terms, and text/accent colors below the configured WCAG contrast checks. Add or edit a JSON theme to adjust the built-in deterministic classifier; restart the service after edits.

The detector scores the current prompt and up to six recent messages. It is keyword/phrase matching, not a separate AI model call, so it adds no model latency or provider cost. A low-confidence short match does not override the active topic. Ordinary switches require repeated evidence, clear switch phrases can move more quickly, and three general turns return to the general theme. Ambiguous short messages can still be misclassified; users can select a manual theme or disable automatic themes.

The `/theme` command supports:

```text
/theme                   Show the current mode and available themes
/theme auto              Resume automatic topic themes
/theme programming       Pin a built-in theme (manual mode)
/theme off               Disable adaptive selection
/theme learning off      Stop favorite-based theme preference updates
/theme reset             Remove visual preferences and return to auto
```

Theme mode and current topic, last confidence, favorite/preferred theme, appearance mode, palette and animation preference are stored per user in SQLite's preferences table. `/theme learning off` does not erase the saved preference; `/theme reset` removes the theme and appearance preference keys. It does not delete chat history, memory, or other account data.

The Mini App provides system/dark/light appearance, theme selection, favorites, palette, animation (full/reduced/off), learning controls, reset, streaming chat, and a private task list with priority/deadline, completion, and deletion controls. Telegram `/tasks plan <goal|chat>` can create up to ten budgeted, dependency-linked steps; `chat` uses only bounded private history. It proposes only stored tasks and does not execute external actions. Task prerequisite rules are checked by the same database layer used by Telegram commands; another Telegram account cannot read or mutate the task. Its responsive canvas effects respect reduced-motion and low-power settings. The theme also adds a small icon/label to bot and support rich-message headings. Telegram clients choose native chat colors; the bot cannot set Telegram wallpaper or change native client theme controls. The current UI is an animated 2D workspace, not the separately proposed holographic 3D command center.

## Package and local verification

The static app and theme JSON files are runtime package data under `src/enan_ultra/webapp/`. Build the wheel and verify those assets are included before distributing a package. For local API checks use the repository's test suite:

```powershell
uv run --locked --group test pytest tests/test_adaptive_theme.py tests/test_mini_app.py tests/test_telegram_messages.py
```

Live Telegram launch requires BotFather credentials, an HTTPS domain, a working reverse proxy, and any configured AI provider. These integration services are not required for local unit tests.
