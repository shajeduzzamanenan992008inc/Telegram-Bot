# Admin guide

## Configure access

Set `ADMIN_CHAT_ID` to a private admin group or chat, and `ADMIN_USER_IDS` to the numeric Telegram user IDs allowed to administer the bot. Keep all credentials in the protected environment file. The installer keeps `/etc/telegram-ai/bot.env` owned by root with mode `0600`.

Admin permissions are enforced by handlers through `is_admin()`; a username or a reply in the admin chat is not sufficient by itself. Support reply routing also requires that the replied-to message ID exists in the database map.

## Commands

- `/admin` lists the main admin actions.
- `/status` shows active live sessions and feature flags.
- `/providers` checks configured provider health, model and cached probe latency. It does not show API keys.
- `/providerdiag` reads local provider circuit/cooldown and credential counts without probing. `/providerdiag recheck <provider>` clears that provider's quarantined-key state and makes a fresh model-list check.
- `/models` lists available models; `/model provider::model-id` changes the selected model after catalog validation.
- `/usage` shows the calling admin's account usage.
- `/vm status|logs|restart` provides sanitized status/logs; restart requires a separate `/approve ID` action.
- `/block USER_ID [reason]` and `/unblock USER_ID` control bot access.
- `/end USER_ID` and `/endall` end live support sessions.
- `/device` can access only allowlisted Home Assistant entities and services; state changes require approval.
- Sticker catalog administration: `/collect_on` and `/collect_off` toggle admin-only, private-chat collection; `/import_sticker_set <short_name>` imports through the Bot API; `/cancel_sticker_import` requests a batch-boundary cancellation. Use `/sticker_review`, `/sticker_categorize ID CATEGORY [tags]`, `/sticker_search`, `/sticker_stats`, and private-chat-only `/sticker_export` to review and manage the catalog. Every operation rechecks `ADMIN_USER_IDS`; imported/collected stickers stay pending until an admin categorizes them.
- Mission/evolution oversight: `/learning_status|pause|resume`, `/evolve_status`, `/evolve_pending`, `/evolve_review ID`, `/evolve_test ID`, `/evolve_approve ID PATCH_SHA256`, `/evolve_stage ID PATCH_SHA256`, `/evolve_deploy_approve ID PATCH_SHA256`, and `/evolve_cancel ID PATCH_SHA256`. Every decision binds to the exact candidate hash. Production approval is recorded only; use the operator's separate deployment process.

## Reply to a user

1. The user starts `/live` or sends a configured support trigger.
2. The bot sends a formatted support notice to `ADMIN_CHAT_ID` and stores its message ID to target chat mapping.
3. Reply directly to that notice. Text is sent with the `ADMIN RESPONSE` layout, UTC timestamp and support reference. A media reply is copied to the user.
4. The admin chat confirms delivery or reports failure. A delivery audit entry records the actor, target and outcome without storing the reply body.

The user must have an active live session. `/exit` ends the user's session; `/end USER_ID` ends it from the admin side. Replies to unrelated admin messages are not forwarded.

## Operational privacy

`/providers`, dashboard output and logs do not include provider keys. Avoid pasting secrets into support replies. User text copied into a support notification is HTML-escaped before rich formatting. The admin dashboard does not expose conversation or Personal KB content.
