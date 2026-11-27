# Opt-in style profile

The style profile is separate from conversation history and long-term factual memory. It is off by default and requires `/style profile on` in a private chat. Opt-in applies only to future private text; it does not scan or backfill previous messages.

## What is stored

After consent, a deterministic local parser looks only for clear style directives in plain text. It can store finite values for reply style (`natural`, `concise`, `detailed`), language (`bn`, `en`, `auto`), emoji frequency, response format (`text`, `markdown`), and sticker mode. It never stores source text, names, identifiers, credentials, health/financial facts, attachments, or model output. Messages matching common sensitive-data patterns are skipped. No provider or model call is used for style extraction.

The profile is stored as a versioned JSON value in the existing per-user `user_preferences` table; no schema migration is required. Each update renews a 90-day expiry. Expired profiles are deleted on access or by the regular maintenance task. Preferences are included in `/privacy export`; `/privacy delete confirm` deletes them with the account's other preferences.

## Controls and application

- `/style profile on` — explicit opt-in for future private text and private Mini App prompts.
- `/style profile status` — shows whether collection/application is on, the finite saved values, and expiry.
- `/style profile off` — stops collection and use but retains the current profile until expiry.
- `/style profile delete` — immediately deletes the profile and disables collection.
- `/resetpreferences` also deletes the style profile.

An enabled profile can affect tone, language, response format, emoji frequency, and sticker mode in private bot replies and Mini App responses. It is not applied in group chats or `/temporary on` sessions. Temporary sessions do not update the profile. Existing explicit preference commands continue to work whether the profile is enabled or not.

The profile does not replace ordinary message history. Private conversation history remains governed by `/privacy`, `/clear`, `/reset`, and `/temporary`; profile deletion does not delete that history. The feature does not train or fine-tune model weights.

## Verification limits

`tests/test_style_profile.py` and the related theme/Mini App tests use temporary SQLite databases and mocked Telegram/provider paths. They do not prove Telegram live behavior, provider behavior, Azure compatibility, Linux/systemd retention execution, or production recovery.
