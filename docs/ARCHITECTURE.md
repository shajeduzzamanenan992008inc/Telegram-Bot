# Architecture

## Request flow

1. `runtime.build_application()` builds PTB, opens SQLite, creates provider/file/action services, and registers commands and background jobs.
2. `BotHandlers` checks authorization, blocked state, admin permissions, privacy preferences, request cooldown and budget before a provider call.
3. `ProviderRouter` applies privacy mode, provider order, modality/capability, free/cost policy, attempt budget, and circuit state before invoking an adapter. Invalid credentials are quarantined; account quota/rate responses cool down that provider and may fall through to the next eligible candidate. Adapters cache health probes for 30 seconds.
4. Text output streams through a token callback. In a private chat, `TelegramRichMessageService` sends one stable draft ID to `sendRichMessageDraft`; in groups it refreshes the typing action.
5. On success, usage is finalized, history is saved, and the full response is sent through the shared formatter with `sendRichMessage`. A plain message is used when the rich endpoint or markup is rejected.

## Adaptive themes and Mini App

`services/theme_engine.py` loads validated JSON themes from `webapp/themes/`, detects topics from bilingual keywords in the current message and recent bounded history, and persists each user's mode, current theme, confidence and visual preferences in `user_preferences`. Hysteresis requires repeated evidence before ordinary topic changes, while clear topic-switch cues can move more quickly. The detector is deterministic and does not make a separate model call. Assistant and support messages add a theme icon/label; Telegram message colors remain controlled by the Telegram client.

When `MINI_APP_URL` is configured, `runtime.py` starts `MiniAppServer` on the separate loopback port. Telegram opens it through `/app`, the `/start` button and the menu button. The server validates signed Telegram `initData` on each API request, checks freshness and user authorization, and uses the authenticated Telegram user ID for preferences, history and chat. It does not use the dashboard session or accept a client-supplied identity. See [Mini App deployment and API](MINI_APP.md).

## Cancellation

`BotHandlers._run_generation()` owns a cancellable provider task per chat. `/cancel` checks that chat's active task and cancels it. Bot API 10.3 stop updates arrive as unknown PTB 22.8 fields in `Update.api_kwargs`; `on_extension_update()` validates the chat and draft IDs before cancelling the matching task. `__main__.main()` explicitly subscribes to this update type. Provider cancellation returns the partial stream to the normal persistence and final-message path.

## Admin support

`/live` and `_notify_admin()` send a support notice using `ResponseFormatter.support_request()`, which HTML-escapes user name and message text. The resulting Telegram message ID maps to the target chat in SQLite. Only an authorized admin can use a reply to a mapped notice; text replies use `ResponseFormatter.admin_reply()` and the same rich-send service. Delivery results are written to the audit table.

## Services and data

| Module | Responsibility |
| --- | --- |
| `handlers.py` | Commands, user/admin authorization, chat orchestration, streaming and support routing |
| `services/telegram_messages.py` | Assistant/support/admin formatting, rich API compatibility and plain fallbacks |
| `services/document_export.py` | In-memory Markdown, UTF-8 text and Bengali-capable PDF output |
| `services/providers.py` | Provider adapters, model selection, failover and cached provider health |
| `services/openrouter.py` | OpenRouter API, model catalog, web tools and media endpoints |
| `services/key_rotation.py`, `services/provider_errors.py` | Invalid-key quarantine, bounded transient retries, Retry-After parsing, sanitized provider error categories and per-request attempt budget |
| `files/registry.py` | File-type adapters and extraction limits |
| `actions.py` | Tool input validation, authorization, approvals, one-shot execution, and structured verified/unverified/failed outcomes |
| `storage/database.py` | SQLite schema/migrations, chat history, usage, support map, memory and audit state |
| `dashboard.py` | Loopback admin dashboard and private operations API |
| `mini_app.py` | Optional loopback Telegram Mini App API, signed identity validation and streaming chat |
| `services/theme_engine.py` | Bilingual topic scoring, theme hysteresis, registry validation and per-user visual preferences |
| `runtime.py` | Startup/shutdown, scheduled maintenance, backup and resource alerts |

The self-contained Mini App frontend lives in `webapp/static/`; editable theme palettes and topic terms live in `webapp/themes/*.json` and are validated at startup.

The saved conversation history is bounded by both `HISTORY_MESSAGES` and an aggregate `MAX_INPUT_CHARS` character budget for prior messages supplied to the model. The most recent context is retained first; if a single older message crosses the remaining budget, only its tail is retained with an omission marker. Telegram chat history uses the chat ID (private chats are user-scoped; group history is shared within that group), while personal memories remain user-scoped. The Telegram bot and Mini App share the same history bound. Personal memory and the Knowledge Base are separate opt-in stores. PDF data is created in memory and is not persisted by the exporter.

The system instruction explicitly asks configured models to understand Bengali script, English, Roman Bangla, mixed-language text, informal spelling and follow-ups. It preserves original user text rather than normalizing it. Actual understanding depends on the selected model/provider; the bot does not make a separate spelling-correction request. An empty provider response is handled as a service failure, not as an unintelligible message.
