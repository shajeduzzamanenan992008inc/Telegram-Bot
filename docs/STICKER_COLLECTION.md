# Admin sticker collection

Sticker collection is disabled by default. It uses the existing async SQLite `Database` connection and stores sticker metadata only; it does not download sticker media or retain the surrounding message.

## Collect an individual sticker

1. Add the Telegram account to `ADMIN_USER_IDS`.
2. In a private chat with the bot, send `/collect_on`.
3. Send the sticker to the bot. The collector accepts it only from a configured admin in that private chat.
4. Review the item with `/sticker_review`, then categorize and approve it using `/sticker_categorize ID CATEGORY [tag ...]`.
5. Use `/collect_off` when done. Existing catalog entries are retained.

New items are pending approval. The emoji and pack title/name can suggest a broad category, but do not prove the sticker's meaning. Unknown or uncertain stickers remain `general`; administrators should inspect them before approval.

## Admin commands

- `/collect_on` and `/collect_off`: change the persisted collection setting. New databases start with collection off.
- `/import_sticker_set <short_name>`: fetch the set using python-telegram-bot's Bot API wrapper and add its metadata in batches of 20. Imported items are pending review.
- `/cancel_sticker_import`: stop the current administrator's import at the next batch boundary.
- `/sticker_stats`: show collection state, catalog counts, format counts, pack count, and successful send count.
- `/sticker_search <query>`: search categories, tags, emoji, labels and pack metadata.
- `/sticker_review`: list up to 30 pending items; repeat to inspect the next database page after categorizing reviewed items.
- `/sticker_categorize ID CATEGORY [tag ...]`: set an approved category and optional tags. Valid categories: `greeting`, `happy`, `celebration`, `thanks`, `thinking`, `confused`, `encouragement`, `supportive`, `programming`, `science`, `goodbye`, `general`.
- `/sticker_export`: in a private chat only, send a JSON metadata backup. The export includes Telegram file identifiers needed for restoration but excludes admin user IDs.

All commands validate the sender against `ADMIN_USER_IDS` on the server. Telegram's command menu visibility is not an authorization control.

## Retrieval and privacy

Inline search and automatic replies read approved catalog entries only. Automatic sending also respects user sticker mode, conversation category and cooldown. A successful use updates total use count and one minimal per-user cooldown row; no message body is stored. Collection and catalog writes use the existing async SQLite connection, are included in the scheduled encrypted database backup, and do not require Telethon.

The legacy `stickers_store.db` catalog is imported into the main database once at startup. Its entries remain pending review, and the legacy file is not deleted. If that migration fails, the bot logs the exception category and retries on a later startup.
