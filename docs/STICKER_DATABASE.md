# Sticker catalog database

The catalog is part of the bot's configured SQLite database (`DATABASE_PATH`), not an independently opened database. It uses the existing `aiosqlite` connection, WAL mode, and the project's versioned, idempotent migration sequence.

## Schema

`sticker_registry` stores the Telegram `file_id` needed to send a sticker and a unique, nullable `file_unique_id` used for duplicate detection. It also records associated emoji, set short name/title, Telegram sticker type, animated/video flags, dimensions, category, JSON tags, source, approval status, usage count, creation/last-use timestamps and send failures. Optional Telegram metadata receives safe empty/default values.

Unique and search indexes cover `file_unique_id`, category/approval, pack name, and approval/record order. Old registry tables are extended additively; existing rows are preserved and default to pending review.

`sticker_user_usage` retains only the last successful sticker and timestamp per Telegram user for a persistent cooldown. It does not retain conversation text. User IDs are not included in `/sticker_export`.

## Async and atomicity

Database methods perform SQL through `aiosqlite`; they do not perform synchronous SQLite work inside Telegram handlers. Sticker duplicate checks and writes are serialized within the database instance and the unique index protects unique IDs. Pack downloads are Bot API requests, not media downloads. Import processing yields to the event loop every 20 items.

The one-time legacy catalog read runs in a worker thread; all new writes go through `Database`. The main database is covered by the configured encrypted scheduled backup when `APP_DATA_KEY` is configured.
