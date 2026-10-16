# Sticker collection troubleshooting

## Collector does not save a sticker

- Verify the sender's numeric ID is listed in `ADMIN_USER_IDS`; usernames and admin-chat membership alone are not sufficient.
- Collection only accepts stickers sent to the bot in a private chat.
- Run `/sticker_stats` and check that collection is on. A fresh install starts off; enable it with `/collect_on`.
- Collected items are pending review, not immediately available for sending.
- Check sanitized bot logs for database failure categories. No token or sticker message text is logged.

## Pack import failed

- Use the sticker set short name, not its share URL, title, or `t.me/addstickers/` prefix.
- Verify the name uses letters, numbers or underscores and that the bot can access the set through Bot API.
- Telegram errors (including invalid sets and rate limits) are bounded and are not retried indefinitely. Wait and retry once the API is available.
- `/cancel_sticker_import` stops at a batch boundary; the already imported items remain pending review.

## Sticker not returned or sent

- Check `/sticker_review` and approve it with `/sticker_categorize`.
- Use `/sticker_search` to check its category and tags; automatic selection is conservative and category-based.
- The user can disable sending with `/stickers off`; `/stickers smart` and `/stickers frequent` set the modes. Both enforce cooldowns.
- A send failure increments the item's failure count. Items with three failures are excluded from approved retrieval; check the catalog and Telegram file ID before relying on them again.
- `/sticker_stats` reports total/approved/pending catalog counts and send totals.

## Backup and migration

The catalog is stored in `DATABASE_PATH` and included in normal database backup/restore. Keep `APP_DATA_KEY` safe for encrypted backups. On startup, any old `stickers_store.db` catalog is migrated into the main database pending review; the old file is retained as a fallback.
