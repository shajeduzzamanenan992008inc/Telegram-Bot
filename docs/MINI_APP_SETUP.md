# Mini App setup

The Mini App is an optional adaptive workspace, not required for ordinary Telegram bot chat. Its complete deployment, reverse-proxy, signed identity, endpoint, and theme documentation is in [MINI_APP.md](./MINI_APP.md).

Minimum deployment:

1. Configure `MINI_APP_URL` as the public HTTPS origin without a path, set `MINI_APP_HOST=127.0.0.1`, and choose an unexposed `MINI_APP_PORT`.
2. Put a TLS reverse proxy in front of that loopback listener; preserve the public `Host` and `X-Forwarded-Proto: https`.
3. Restart the service and open `/app` from a private Telegram chat.

The browser sends Telegram's signed `initData`; the backend validates it and derives the user identity from the signed payload. Do not expose the ASGI port directly or add client-supplied user IDs as an authorization source. Without a valid Telegram Web App session the UI disables chat.

The workspace includes streaming chat, topic-aware visual themes, appearance/motion/palette controls, favorites, and a personal task list. It follows the normal provider, budget, privacy, history and temporary-chat rules. Theme changes inside the browser do not alter Telegram's native wallpaper or client theme.
