# Telegram sticker-set import

The bot uses python-telegram-bot 22.8 and the official Bot API `get_sticker_set` wrapper. No user-account session or Telethon dependency is used.

## Usage

An administrator runs:

```text
/import_sticker_set FunnyPack_by_Bot
```

The short name accepts 1–64 ASCII letters, digits, or underscores. Telegram returns the set title, short name and sticker objects; the importer stores the available file IDs, unique IDs, emoji, sticker format/type and dimensions. It does not download sticker media. All imported items are marked pending review. Pack name/title can suggest a category, but the emoji/category relationship is not treated as reliable ground truth.

The command reports discovered, inserted, duplicate-skipped and failed counts. Per-item database errors increment `failed`; malformed short names and invalid pack names receive clear responses; Telegram API/rate-limit/network failures are reported without unbounded retries. A repeated concurrent import of the same set is rejected. `/cancel_sticker_import` requests cancellation at the next batch boundary.

To enable the imported stickers for retrieval, inspect `/sticker_review` and run `/sticker_categorize ID CATEGORY [tags]`. `/sticker_stats` summarizes the catalog. The feature does not delete catalog items automatically.
