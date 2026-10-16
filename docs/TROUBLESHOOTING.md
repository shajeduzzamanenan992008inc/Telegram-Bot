# Troubleshooting

## Rich preview is missing

- Confirm the conversation is a private chat. Group chats intentionally use typing status instead.
- Rich Messages require Bot API 10.1+. If `LOCAL_BOT_API_BASE_URL` points to an older local server, the bot falls back to ordinary text drafts/messages.
- PTB 22.8 does not provide typed Rich Message methods; the bot uses the library request transport. A rejected Rich API request is logged by exception class and falls back.
- An older Telegram client may render fewer rich features. The final answer is still sent as a regular Telegram message if the server rejects rich formatting.

## Stop control or `/cancel` does not stop a request

The native stop button requires Bot API 10.3 and the `stopped_message_generation` update. The command-line entry point subscribes to it and validates chat/draft IDs. `/cancel` works with a supported running provider request even when the client does not show the control. A provider that ignores task cancellation can delay completion.

## Admin reply did not arrive

Reply directly to the bot's support notification in `ADMIN_CHAT_ID`. Check that the sender ID is in `ADMIN_USER_IDS`, the live session has not ended, and the mapping message is still available. The admin chat receives a delivery result; failed sends are logged by exception class and audited.

## PDF is missing Bengali glyphs

On Ubuntu, rerun the installer to install `fonts-noto-core`. On Windows, ensure Nirmala UI is installed. Restart the bot after adding a system font. PDF generation requires `reportlab[shaping]`, which is pinned in `pyproject.toml` and `uv.lock`.

## `/providers` times out or shows unavailable

Provider health is a model-list probe cached for 30 seconds. Check DNS, provider endpoint, credentials and rate limits. The command never prints credentials. A transient provider outage does not prevent configured fallback providers from handling requests.

## Roman Bangla, follow-ups, or image understanding seem inconsistent

- The bot sends the original message to the configured current model with a system instruction for Bengali, English, Roman Bangla, mixed language and informal spelling. It does not call a separate spell-correction service. Actual interpretation quality depends on the selected model.
- Check `/model` and `/models`; `DEFAULT_AI_MODEL=auto` selects according to provider availability, capability and cost policy, not a guaranteed Roman Bangla benchmark. Choose an available instruction-following model that supports Bengali using `/model provider::model-id` if the current model performs poorly.
- Normal chat history is scoped to the Telegram chat; private chats are user-specific and group conversations share that group's history. Only the configured recent message window and `MAX_INPUT_CHARS` aggregate history budget are sent. `/reset` clears the current conversation.
- Photo input uses `VISION_MODEL`. Confirm that the configured model supports image input and is allowed by the active provider/cost policy.
- A provider timeout or empty response is a temporary service failure; it does not mean the message was unintelligible. Check the sanitized provider logs and `/providerdiag` for availability, rate-limit or credential issues.

Use admin `/providerdiag` to inspect local circuit state, rejected-key counts, and cooldown without making a network request. A 429 or quota/billing response pauses that provider and does not rotate through sibling keys because they may share the same account quota. Check the provider account/project limits, then wait for the retry hint/cooldown. For a replaced or restored credential, `/providerdiag recheck <provider>` clears its quarantine and makes a model-list probe; a still-invalid key will be quarantined again. If the model is unavailable, verify its exact provider-qualified ID and whether the account has access.

## A model is skipped in free-only mode

With `AI_FREE_ONLY=true`, cloud models must match an exact entry in `AI_FREE_MODEL_ALLOWLIST`, such as `groq::model-id`. Review the current provider model ID and billing status, then update the local `.env` allowlist deliberately. If paid fallback is enabled, set a positive `AI_MAX_ESTIMATED_COST_USD`; unknown prices are not approved for paid fallback. The estimate is a routing guardrail, not an invoice cap.

## Service or configuration problems

```sh
sudo systemctl status telegram-ai
sudo journalctl -u telegram-ai -n 200 --no-pager
```

Check required environment values, read permissions, `DATABASE_PATH`, and provider configuration. Keep `APP_DATA_KEY` unchanged when restoring encrypted backups. Do not paste tokens or full environment files into logs or support messages.

## Mission is paused after a restart

This is expected recovery behavior. The bot does not blindly replay unfinished work. The mission owner can inspect `/mission_status ID` and explicitly resume with `/mission_resume ID`. Mission Control only performs text analysis; it cannot inspect local server files or run commands.

## Evolution proposal cannot be approved

Use `/evolve_review ID` and verify the patch hash exactly. The proposal needs a passing rootless sandbox test and must be unexpired and still in the expected state. Protected security/configuration/storage/handler/sandbox/deployment paths are rejected from automated evolution. Configure and build the trusted sandbox image before `/evolve_test ID` or `/evolve_stage ID HASH`.

## Production approval did not deploy

`/evolve_deploy_approve` records a distinct admin decision only. The current repository has no automated staging or release backend. Apply the reviewed candidate through the operator deployment procedure and verify service health manually.
