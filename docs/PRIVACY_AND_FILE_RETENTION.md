# Privacy and file retention

Use `/privacy` to review the active data flow. In cloud mode, text, transcribed audio, extracted document passages or image content may be sent to configured remote providers for the requested task. Local-only mode blocks remote fallback for supported chat/media paths; unsupported local capability is reported rather than silently routed to cloud.

- Private `/temporary on` chat does not save new message history or retrieve prior memory, saved personal KB passages, or temporary document indexes. It does not make an external provider request local: the selected privacy/provider mode still applies.
- Parsed upload text indexes are bounded by `DOCUMENT_RETENTION_HOURS`; `/files` lists current chat indexes and `/forget_files` removes them. Raw uploads are normally processed in memory; OCR/ffmpeg temporary files are scoped to temporary directories and removed after processing.
- Personal KB saving is separate and explicit (`/kb on`, then `/kb save ID`); stored KB data is encrypted with the configured application key and scoped to the owner.
- Sticker collection is disabled by default and can be enabled only by a configured admin. In enabled mode, only admin-submitted private-chat sticker metadata or explicitly imported public pack metadata is retained; sticker media and message text are not downloaded/stored. The main SQLite database stores a numeric admin actor for catalog provenance and a single last-use row per user for cooldowns. User data export includes their cooldown metadata; account deletion removes it and anonymizes matching sticker provenance. Admin `/sticker_export` excludes actor IDs.
- Optional ClamAV checks run before upload parsing/provider forwarding. Enabled scanner failure is fail-closed.
- Remote provider file uploads are subject to configured privacy/fallback behavior and are queued for provider-side cleanup if immediate deletion fails.
- `/privacy export` exports user-linked data; `/privacy delete confirm` deletes user-linked data. Shared group history is not erased as part of an individual account deletion.
- `/style profile on` is required before local style extraction. The saved profile contains finite preference values only, expires 90 days after its last update, and can be removed with `/style profile delete`; this does not delete normal conversation history.

Provider request retention is also governed by the selected provider's policy and is outside the bot's SQLite retention controls. Keep API keys and `APP_DATA_KEY` private; losing the latter makes encrypted KB data/backups unavailable. See [deployment](./DEPLOYMENT_AZURE_VM.md), [configuration](./CONFIGURATION.md), and the [README](../README.md#defense-in-depth).
