# Application security boundaries

This note documents existing application controls; it is not a penetration-test report.

- Mini App APIs require Telegram-signed `initData`; the server checks signature, freshness, duplicate fields, authorization and blocked-user state. User IDs are derived only from the signed payload.
- Provider credentials and bot tokens remain server-side. API responses use restrictive security headers and no-store caching; the web app does not authenticate with cookies or `initDataUnsafe`.
- User chat history, preferences, task lists and Personal KB access are keyed by authenticated Telegram user/chat scope. Group chat history is intentionally shared within that group.
- Upload paths apply size/type/parser limits; optional enabled ClamAV scanning fails closed. Archives are bounded and checked for unsafe member paths; uploaded code is read as data and is not executed by normal document processing.
- Retrieved documents, memory values and uploaded text are untrusted context, not system instructions. AI tool actions retain existing authorization and approval checks.
- Local-only mode prevents remote model fallback and other remote processing where the relevant handler supports that privacy setting. Check `/privacy` and [privacy and retention](./PRIVACY_AND_FILE_RETENTION.md) before enabling remote services.

Deploy Mini App only behind HTTPS and keep its ASGI listener bound to loopback. For operational hardening and service deployment, follow [Mini App setup](./MINI_APP_SETUP.md) and [Azure VM deployment](./DEPLOYMENT_AZURE_VM.md).
