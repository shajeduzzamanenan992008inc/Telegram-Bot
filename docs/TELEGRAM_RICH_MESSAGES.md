# Telegram Rich Messages and live previews

The bot uses Telegram's [Rich Messages API](https://core.telegram.org/bots/api#rich-messages), introduced in Bot API 10.1, and the stop-generation fields added in Bot API 10.3.

## Runtime behavior

- Before a provider request starts producing tokens in a private chat, the bot sends `<tg-thinking>Thinking…</tg-thinking>` as a rich draft.
- Provider token callbacks update the same nonzero `draft_id`, at most once every 1.5 seconds. After the first token, the preview includes only generated output; it never displays hidden reasoning.
- Draft updates set `can_stop=true` and `keep_on_stop=true`. The command-line runner subscribes to `stopped_message_generation`; PTB 22.8 retains the unknown object in `Update.api_kwargs`, where the extension handler checks the draft and chat IDs before cancelling.
- If a Bot API 10.1/10.2 server rejects those Bot API 10.3 flags, the service retries the same draft without stop controls; `/cancel` remains available.
- Once generation ends, the bot sends the complete response through `sendRichMessage`. Drafts expire, so this final send is required to persist the answer.
- `/cancel` is available if a client does not show the stop control. It also cancels non-streaming provider requests.

## Compatibility and fallbacks

This repository pins `python-telegram-bot==22.8`, which provides `send_message_draft()` but does not expose Bot API 10.1 Rich Messages as typed methods. `TelegramRichMessageService` therefore calls the library's shared `_post()` request pipeline with `sendRichMessage` and `sendRichMessageDraft`; this preserves the configured cloud/local Bot API endpoint, HTTP client and token handling.

If a server rejects a rich draft, the bot tries `sendMessageDraft` and keeps the typing action as a fallback. If a rich final response is rejected, it sends ordinary Telegram text. Rich drafts are private-chat-only. Group responses use `sendChatAction` while work continues and an ordinary final response. The client determines how rich content looks; the Bot API does not offer arbitrary font colors. Standard inline keyboard buttons are used for the `/start` menu.

If a request times out at the network layer, Telegram may have accepted it before the connection failed. The bot does not automatically repeat every uncertain request, which avoids duplicate final messages.

## Limits

- Rich draft is temporary; successful replies are persisted with a separate final request.
- Rich Markdown that Telegram cannot parse falls back to plain text.
- Local Bot API server releases must support the rich methods and stop update type to show the full experience. Older releases continue through fallbacks; `/cancel` remains available.
- Native Rich Message media blocks and rich inline buttons are not used for AI replies. Existing image, voice and document paths still send Telegram's normal media messages.

## Manual smoke check

With a test bot token and a supported Telegram client, send a prompt that takes several seconds. Confirm a Thinking preview appears, text updates without creating extra messages, the stop control cancels generation, and the final response remains in chat. Then repeat in a group and confirm a typing action plus ordinary final message. Automated tests mock the Bot API; they do not contact Telegram.
