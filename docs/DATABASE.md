# SQLite database reference

The application persists data in SQLite through the async `Database` class in
[`database.py`](../src/enan_ultra/storage/database.py). The schema is created
and evolved by SQL in `Database.migrate()`; the repository does not use SQLAlchemy
models or Alembic migrations. Table ownership described below is enforced by
application queries and handlers unless a database constraint is explicitly
listed.

## Initialization and migrations

`Database.open()` creates the database parent directory, opens SQLite, enables
WAL mode, enables foreign-key enforcement, sets a 5-second busy timeout, applies
owner-only filesystem permissions on non-Windows systems, and calls
`Database.migrate()`. Startup migration is automatic; there is no separate
migration CLI or documented downgrade procedure.

The migration code creates tables/indexes idempotently and records schema
milestones 1 through 16 in `schema_version`. For historical columns it inspects
`PRAGMA table_info` before applying `ALTER TABLE`, including reminders, tasks,
the admin reply map, sticker metadata and evolution proposal source hash.
`schema_version` records applied milestones; it is not an Alembic-style migration
script ledger. Version 16 adds a SHA-256 hash chain to audit events and backfills
existing rows in ID order. Back up the database before changing application versions. Do
not edit production schema manually based on this reference.

The database uses parameterized SQLite queries for normal data access; code has
no general-purpose ORM relationship layer. SQLite foreign keys are enabled for
connections opened by the application. Only the following explicit FK/cascade
relationships exist in the current migration SQL:

- `document_chunks.document_id` → `documents.id` (`ON DELETE CASCADE`).
- `kb_chunks.document_id` → `kb_documents.id` (`ON DELETE CASCADE`).
- `task_dependencies.task_id` and `.depends_on_task_id` → `user_tasks.id`
  (`ON DELETE CASCADE` for both).
- `mission_steps.mission_id` → `missions.id` (`ON DELETE CASCADE`).
- `sticker_user_usage.sticker_id` → `sticker_registry.id` (no cascade clause).

Other ownership references are logical, implemented through user/chat-scoped
queries, and are not foreign-key constraints.

## Tables and access boundaries

| Table | Purpose and logical ownership |
| --- | --- |
| `schema_version` | Migration milestone number (primary key) and applied timestamp. |
| `processed_updates` | Telegram update IDs and processing time for duplicate suppression; bounded cleanup defaults to 30 days. |
| `messages` | Private or chat-scoped user/assistant conversation history; `chat_id` is the scope key. Writes retain only the configured recent message count (minimum two rows). |
| `group_messages` | Bounded recent group-chat messages with sender ID/display name; shared by that chat, not a private-user-owned transcript. The application retains 50–100 rows per chat. |
| `chat_settings` | One mode and update time per chat ID. |
| `live_sessions` | One active/inactive session row per user ID. |
| `admin_reply_map` | Maps an admin message ID to a user chat, user ID and theme ID for reply routing. |
| `settings` | Global key/value settings and update timestamps. |
| `update_upload_gate` | Singleton persistent ZIP upload gate: disabled/open/processing state, administrator who opened it, expiry, and one-shot processing token/update key. Atomic SQLite transitions prevent concurrent uploads; interrupted processing is closed during startup recovery. |
| `user_preferences` | Per-user key/value preferences, including privacy, appearance and opted-in style profile values; composite primary key `(user_id,key)`. |
| `sticker_registry` | Shared sticker catalogue metadata, approval/category/tags and usage counters. It stores Telegram file references and catalogue data, not the submitted message text. |
| `memories` | User-scoped long-term facts. |
| `user_memories` | User-scoped keyed profile facts; composite primary key `(user_id,key)`. |
| `learned_knowledge` | User-scoped fact key/value records with optional source user ID; unique `(user_id,category,fact_key)`. |
| `documents` | Chat-scoped extracted document metadata and expiry. |
| `document_chunks` | Extracted content chunks linked to `documents`; deleted with the parent document. |
| `reminders` | User/chat reminder text, due/delivery/retry state and optional recurrence. |
| `usage_events` | User/model token and cost accounting events. |
| `approvals` | Request/approval state, approver and expiry. The payload is encrypted when `APP_DATA_KEY` is configured. |
| `agent_jobs` | User/task/provider/status and bounded job summary, step and cost metadata. |
| `feedback` | User rating and normalized category only; it does not store rated message/answer text. |
| `kb_documents` | User-owned Personal KB document metadata; filename and MIME type are stored as encrypted BLOBs by the database service. |
| `kb_chunks` | User KB source/content encrypted as BLOBs; linked to KB document with cascade deletion. |
| `audit_events` | Actor, action, target, outcome, timestamp, previous-row hash and event hash. `Database.verify_audit_chain()` detects changed, missing, or reordered rows relative to the stored chain. This is not an external immutable anchor: an attacker able to rewrite the database and recompute hashes can forge a new chain. User-data deletion removes that user's audit rows and re-chains the remaining rows atomically to honor deletion. |
| `action_executions` | One-shot tool/action execution state per chat/message/action key. |
| `blocked_users` | Moderation status/reason per user. Intentionally retained on account deletion to preserve the abuse block. |
| `user_tasks` | User-owned task text/status/priority/deadline and timestamps. |
| `study_cards` | User-owned question/answer and review scheduling state. |
| `task_dependencies` | User task prerequisite links; composite primary key, no self-dependency, both task references cascade on deletion. |
| `sticker_user_usage` | Most recently used sticker per user; one row per user, references the catalogue sticker. |
| `missions` | User-owned mission metadata and encrypted payload; includes a task fingerprint for duplicate detection. |
| `mission_steps` | Per-mission step status/details/results; details/results are encrypted BLOBs and rows cascade with the mission. |
| `evolution_proposals` | User-created proposal state, hashes, encrypted patch/archive payload and encrypted test report. |

### Constraints and indexes

The current schema declares these notable checks/uniqueness rules:

- `messages.role` is `user` or `assistant`.
- `live_sessions.active`, `reminders.delivered`, and `reminders.paused` are
  booleans represented by 0/1 checks.
- `approvals.required_approver` and `approvals.status`, `action_executions.status`,
  `feedback.rating`, `user_tasks.status` and `user_tasks.priority`,
  `missions.state`, `mission_steps.state`, and `evolution_proposals.state` use
  enumerated `CHECK` constraints.
- `action_executions` is unique on `(chat_id,message_id,action_key)`.
- `task_dependencies` is unique on `(task_id,depends_on_task_id)` and rejects
  self-dependency.
- `sticker_registry.file_unique_id` has a partial unique index when non-NULL.
- `missions` has a partial unique index on `(owner_id,task_fingerprint)` while
  state is `planning`, `running`, or `paused`.

Indexes created by the migrations (in addition to primary-key/unique indexes):

| Index | Columns / predicate |
| --- | --- |
| `idx_processed_updates_time` | `processed_updates(processed_at)` |
| `idx_messages_chat_created` | `messages(chat_id,created_at DESC)` |
| `idx_group_messages_chat_id` | `group_messages(chat_id,id DESC)` |
| `idx_memories_user` | `memories(user_id,id DESC)` |
| `idx_user_memories_user` | `user_memories(user_id,key)` |
| `idx_learned_knowledge_scope` | `learned_knowledge(user_id,category,updated_at DESC)` |
| `idx_learned_knowledge_source` | `learned_knowledge(source_user_id)` |
| `idx_doc_chunks_document` | `document_chunks(document_id)` |
| `idx_reminders_due` | `reminders(delivered,run_at)` |
| `idx_usage_user_created` | `usage_events(user_id,created_at DESC)` |
| `idx_approvals_status_expiry` | `approvals(status,expires_at)` |
| `idx_agent_jobs_created` | `agent_jobs(created_at DESC)` |
| `idx_feedback_created` | `feedback(created_at DESC)` |
| `idx_kb_documents_user` | `kb_documents(user_id,id DESC)` |
| `idx_kb_chunks_document` | `kb_chunks(document_id)` |
| `idx_audit_created` | `audit_events(created_at DESC)` |
| `idx_action_executions_created` | `action_executions(created_at)` |
| `idx_user_tasks_open` | `user_tasks(user_id,status,id DESC)` |
| `idx_study_cards_due` | `study_cards(user_id,due_at,id)` |
| `idx_task_dependencies_prerequisite` | `task_dependencies(depends_on_task_id,task_id)` |
| `idx_sticker_registry_unique_id` | Partial unique `sticker_registry(file_unique_id)` where not NULL |
| `idx_sticker_registry_category` | `sticker_registry(category,approved)` |
| `idx_sticker_registry_set` | `sticker_registry(set_name)` |
| `idx_sticker_registry_status` | `sticker_registry(approved,id DESC)` |
| `idx_missions_owner_created` | `missions(owner_id,created_at DESC)` |
| `idx_missions_state_updated` | `missions(state,updated_at)` |
| `idx_missions_active_fingerprint` | Partial unique `(owner_id,task_fingerprint)` for active states |
| `idx_mission_steps_state` | `mission_steps(mission_id,state,step_index)` |
| `idx_evolution_state_created` | `evolution_proposals(state,created_at DESC)` |

## Retention, export and deletion

- Private chat message writes keep only the latest configured history count;
  temporary-chat mode avoids saving new chat messages. Group history is
  separately bounded to at most 100 rows per chat. There is no global
  time-to-live cleanup for ordinary message/group history shown in the current
  database cleanup calls.
- Extracted document rows expire after `DOCUMENT_RETENTION_HOURS` (default 24
  hours); runtime cleanup deletes expired `documents`, cascading their chunks.
- Processed update IDs default to a 30-day cleanup window. Action execution rows
  default to 90 days. Mission payload expiry is 30 days; terminal mission
  metadata defaults to 90 days. Evolution proposal payload expires after seven
  days; terminal proposal metadata defaults to 90 days. Runtime schedules
  cleanup for these records and also invokes style-profile and theme-metadata
  expiry cleanup.
- `/privacy export` is implemented by `export_user_data()`. It exports
  user-scoped preferences, memories, profile facts, learning facts, reminders,
  tasks, study cards, usage, feedback, missions/proposals, sticker usage, agent
  jobs, KB content, approvals, audit metadata and moderation state. Private
  message history/document chunks are included only when the export is requested
  from that user's private chat.
- `/privacy delete confirm` calls `delete_user_data()` from a private chat.
  User-owned profile, memory, reminders, tasks, usage, study cards, feedback,
  jobs, KB, missions, proposals, approvals, audit and sticker-usage rows are
  removed. When the private chat ID matches the user ID, private chat history,
  extracted documents/chunks, action execution rows and admin reply mappings
  are also deleted. Shared group history is not deleted. The moderation block
  remains in place, and the command's own confirmation message is not removed.
- `/clear` / dashboard clear-memory intentionally has a narrower scope:
  private history and long-term memories/learned facts are cleared; it does not
  delete all account records or shared group history.
- Mission/proposal/KB payload encryption requires a stable `APP_DATA_KEY`.
  Changing or losing that key prevents decrypting retained encrypted values.
  See [`PRIVACY_AND_RETENTION.md`](./PRIVACY_AND_RETENTION.md) and
  [`PRIVACY_AND_FILE_RETENTION.md`](./PRIVACY_AND_FILE_RETENTION.md) for feature
  specific privacy notices and retention details.

## Backup and restore

The repository provides
[`scripts/database_backup.py`](../scripts/database_backup.py), and the runtime
database service also exposes online backup helpers. Both use SQLite's online
backup API and run `PRAGMA integrity_check`; encrypted backups require
`APP_DATA_KEY`. The CLI supports:

```text
python scripts/database_backup.py backup <destination>
python scripts/database_backup.py restore <source> --to <destination>
python scripts/database_backup.py restore <source> --to <destination> --replace
```

Restore refuses plaintext SQLite unless `--allow-unencrypted` is explicitly
provided. `--replace` first preserves an existing destination as a
`.pre-restore-<UTC timestamp>` copy; restoring to the configured live database
is refused while `telegram-ai.service` is active. Keep the same
`APP_DATA_KEY` when restoring encrypted backups. The repository guide recommends
restoring into a separate staging state directory and verifying integrity and
application health before any operational use.

Encrypted backup/restore, the CLI guards and WAL-aware online backup are
implemented but not covered by an automated test in this checkout, and no real
service restore drill has been performed. The repository does not establish a
production backup destination, off-host replication, retention schedule, or
tested live restore procedure. See
[`DEPLOYMENT_AND_ROLLBACK.md`](./DEPLOYMENT_AND_ROLLBACK.md) for the documented
staging-only procedure and operational caveats.

## Source and test references

- Schema, migration SQL, indexes and database methods:
  [`database.py`](../src/enan_ultra/storage/database.py).
- Runtime database initialization and cleanup scheduling:
  [`runtime.py`](../src/enan_ultra/runtime.py).
- User export/deletion commands and privacy scope:
  [`handlers.py`](../src/enan_ultra/handlers.py) and
  [`PRIVACY_AND_RETENTION.md`](./PRIVACY_AND_RETENTION.md).
- Schema version, reopen check, history ordering, reminder leasing and
  user-deletion audit integrity:
  [`test_database.py`](../tests/test_database.py).
- Task ownership and dependency checks are enforced by the `user_tasks` queries
  in [`database.py`](../src/enan_ultra/storage/database.py); there is no
  dedicated automated task-isolation test in this checkout.
- Backup implementation: [`database_backup.py`](../scripts/database_backup.py);
  no automated backup/restore test in this checkout.