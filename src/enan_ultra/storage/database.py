"""Async SQLite persistence with versioned, idempotent migrations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import sqlite3
import stat
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
import uuid

import aiosqlite

from enan_ultra.crypto import DataCipher


def utc_now() -> datetime:
    return datetime.now(UTC)


def _usage_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _usage_float(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return max(0.0, parsed) if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _normalize_sticker_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    for tag in tags:
        value = " ".join(str(tag).split())[:40]
        if value and value.casefold() not in {item.casefold() for item in normalized}:
            normalized.append(value)
        if len(normalized) >= 20:
            break
    return normalized


def _sticker_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    try:
        parsed_tags = json.loads(result.get("tags") or "[]")
        result["tags"] = _normalize_sticker_tags(
            parsed_tags if isinstance(parsed_tags, list) else [],
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        result["tags"] = []
    result["approved"] = bool(result.get("approved", 0))
    result["is_animated"] = bool(result.get("is_animated", 0))
    result["is_video"] = bool(result.get("is_video", 0))
    return result


class Database:
    def __init__(
        self, path: Path, retention_hours: int = 24, max_pending_reminders: int = 20,
        data_key: str = "", kb_max_documents: int = 50, kb_max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.path = path
        self.retention_hours = retention_hours
        self.max_pending_reminders = max_pending_reminders
        self.cipher = DataCipher(data_key) if data_key else None
        self.kb_max_documents = max(1, kb_max_documents)
        self.kb_max_bytes = max(1024, kb_max_bytes)
        self.connection: aiosqlite.Connection | None = None
        self._usage_lock = asyncio.Lock()
        self._kb_lock = asyncio.Lock()
        self._sticker_lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        if os.name != "nt":
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.migrate()

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()
            self.connection = None

    async def health_check(self) -> bool:
        cursor = await self._db().execute("SELECT 1")
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None and int(row[0]) == 1

    async def claim_update(self, update_id: int) -> bool:
        """Atomically claim a Telegram update ID before any handler side effect."""
        if update_id < 0:
            raise ValueError("Telegram update_id must be non-negative")
        cursor = await self._db().execute(
            "INSERT OR IGNORE INTO processed_updates(update_id, processed_at) VALUES(?, ?)",
            (update_id, utc_now().isoformat()),
        )
        claimed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return claimed

    async def cleanup_processed_updates(self, retention_days: int = 30) -> int:
        """Bound the deduplication table while retaining IDs beyond Telegram retry windows."""
        cutoff = (utc_now() - timedelta(days=max(1, retention_days))).isoformat()
        cursor = await self._db().execute(
            "DELETE FROM processed_updates WHERE processed_at < ?", (cutoff,),
        )
        deleted = max(0, cursor.rowcount)
        await cursor.close()
        await self._db().commit()
        return deleted

    async def backup_to(self, destination: Path) -> None:
        """Create a consistent, atomic SQLite backup suitable for encrypted-KB restore."""
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from the live database")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            destination.parent.chmod(0o700)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(temporary, check_same_thread=False)
            if os.name != "nt":
                temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            await self._db().backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed")
            target.close()
            target = None
            if os.name != "nt":
                temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            temporary.replace(destination)
        finally:
            if target:
                target.close()
            temporary.unlink(missing_ok=True)

    async def backup_encrypted_to(self, destination: Path) -> None:
        if self.cipher is None:
            raise RuntimeError("APP_DATA_KEY is required for encrypted backups")
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from the live database")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            destination.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile(
            prefix=".unencrypted-backup-", suffix=".sqlite3", dir=destination.parent, delete=False,
        ) as handle:
            plaintext = Path(handle.name)
        encrypted = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            if os.name != "nt":
                plaintext.chmod(stat.S_IRUSR | stat.S_IWUSR)
            await self.backup_to(plaintext)
            payload = self.cipher.encrypt(plaintext.read_bytes())
            with encrypted.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                encrypted.chmod(stat.S_IRUSR | stat.S_IWUSR)
            encrypted.replace(destination)
        finally:
            plaintext.unlink(missing_ok=True)
            encrypted.unlink(missing_ok=True)

    def _db(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not open")
        return self.connection

    async def migrate(self) -> None:
        db = self._db()
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_updates (
                update_id INTEGER PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_processed_updates_time
                ON processed_updates(processed_at);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_group_messages_chat_id
                ON group_messages(chat_id, id DESC);
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'default',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_sessions (
                user_id INTEGER PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_reply_map (
                admin_message_id INTEGER PRIMARY KEY,
                user_chat_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, key)
            );
            CREATE TABLE IF NOT EXISTS sticker_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                file_unique_id TEXT,
                category TEXT NOT NULL,
                emoji TEXT DEFAULT '',
                label TEXT DEFAULT '',
                added_by INTEGER DEFAULT 0,
                added_at TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                set_title TEXT NOT NULL DEFAULT '',
                sticker_type TEXT NOT NULL DEFAULT 'regular',
                is_animated INTEGER NOT NULL DEFAULT 0,
                is_video INTEGER NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '[]',
                source_type TEXT NOT NULL DEFAULT 'manual',
                approved INTEGER NOT NULL DEFAULT 0,
                usage_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                last_used_at TEXT,
                send_failures INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                fact TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS user_memories (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(user_id, key)
            );
            CREATE INDEX IF NOT EXISTS idx_user_memories_user
                ON user_memories(user_id, key);
            CREATE TABLE IF NOT EXISTS learned_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                fact_key TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                source_user_id INTEGER,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, category, fact_key)
            );
            CREATE INDEX IF NOT EXISTS idx_learned_knowledge_scope
                ON learned_knowledge(user_id, category, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_learned_knowledge_source
                ON learned_knowledge(source_user_id);
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_doc_chunks_document ON document_chunks(document_id);
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                reminder_text TEXT NOT NULL,
                run_at TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0,1)),
                paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
                attempts INTEGER NOT NULL DEFAULT 0,
                claimed_until TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(delivered, run_at);
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage_events(user_id, created_at DESC);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(1, ?)",
            (utc_now().isoformat(),),
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload BLOB NOT NULL,
                required_approver TEXT NOT NULL CHECK(required_approver IN ('requester','admin')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','executing','approved','denied','expired','failed')),
                approver_id INTEGER,
                result TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_status_expiry ON approvals(status, expires_at);
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_type TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                step_count INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_jobs_created ON agent_jobs(created_at DESC);
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL CHECK(rating IN (-1,1)),
                category TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);
            CREATE TABLE IF NOT EXISTS kb_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename BLOB NOT NULL,
                mime_type BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kb_documents_user ON kb_documents(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS kb_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
                source BLOB NOT NULL,
                content BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL DEFAULT 0,
                action TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(2, ?)",
            (utc_now().isoformat(),),
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                action_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running','complete','failed')),
                result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(chat_id, message_id, action_key)
            );
            CREATE INDEX IF NOT EXISTS idx_action_executions_created
                ON action_executions(created_at);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(3, ?)",
            (utc_now().isoformat(),),
        )
        cursor = await db.execute("PRAGMA table_info(reminders)")
        reminder_columns = {str(row["name"]) for row in await cursor.fetchall()}
        await cursor.close()
        if "repeat_seconds" not in reminder_columns:
            await db.execute("ALTER TABLE reminders ADD COLUMN repeat_seconds INTEGER")
        for column, definition in (
            ("paused", "INTEGER NOT NULL DEFAULT 0"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("claimed_until", "TEXT"),
        ):
            if column not in reminder_columns:
                await db.execute(f"ALTER TABLE reminders ADD COLUMN {column} {definition}")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL DEFAULT '',
                blocked_by INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(4, ?)",
            (utc_now().isoformat(),),
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                task_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')),
                priority TEXT NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('low','normal','high','urgent')),
                due_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_user_tasks_open
                ON user_tasks(user_id,status,id DESC);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(5, ?)",
            (utc_now().isoformat(),),
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS study_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                interval_days INTEGER NOT NULL DEFAULT 0,
                due_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_study_cards_due
                ON study_cards(user_id,due_at,id);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(6, ?)",
            (utc_now().isoformat(),),
        )
        cursor = await db.execute("PRAGMA table_info(user_tasks)")
        task_columns = {str(row["name"]) for row in await cursor.fetchall()}
        await cursor.close()
        if "priority" not in task_columns:
            await db.execute(
                "ALTER TABLE user_tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'"
            )
        if "due_at" not in task_columns:
            await db.execute("ALTER TABLE user_tasks ADD COLUMN due_at TEXT")
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(7, ?)",
            (utc_now().isoformat(),),
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(8, ?)",
            (utc_now().isoformat(),),
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(9, ?)",
            (utc_now().isoformat(),),
        )
        cursor = await db.execute("PRAGMA table_info(admin_reply_map)")
        admin_map_columns = {str(row["name"]) for row in await cursor.fetchall()}
        await cursor.close()
        if "user_id" not in admin_map_columns:
            await db.execute("ALTER TABLE admin_reply_map ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
        if "theme_id" not in admin_map_columns:
            await db.execute("ALTER TABLE admin_reply_map ADD COLUMN theme_id TEXT NOT NULL DEFAULT ''")
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(10, ?)",
            (utc_now().isoformat(),),
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id INTEGER NOT NULL REFERENCES user_tasks(id) ON DELETE CASCADE,
                depends_on_task_id INTEGER NOT NULL REFERENCES user_tasks(id) ON DELETE CASCADE,
                PRIMARY KEY(task_id, depends_on_task_id),
                CHECK(task_id != depends_on_task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_task_dependencies_prerequisite
                ON task_dependencies(depends_on_task_id, task_id);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(11, ?)",
            (utc_now().isoformat(),),
        )
        cursor = await db.execute("PRAGMA table_info(sticker_registry)")
        sticker_columns = {str(row["name"]) for row in await cursor.fetchall()}
        await cursor.close()
        sticker_migrations = (
            ("file_unique_id", "TEXT"),
            ("set_name", "TEXT NOT NULL DEFAULT ''"),
            ("set_title", "TEXT NOT NULL DEFAULT ''"),
            ("sticker_type", "TEXT NOT NULL DEFAULT 'regular'"),
            ("is_animated", "INTEGER NOT NULL DEFAULT 0"),
            ("is_video", "INTEGER NOT NULL DEFAULT 0"),
            ("width", "INTEGER NOT NULL DEFAULT 0"),
            ("height", "INTEGER NOT NULL DEFAULT 0"),
            ("tags", "TEXT NOT NULL DEFAULT '[]'"),
            ("source_type", "TEXT NOT NULL DEFAULT 'manual'"),
            ("approved", "INTEGER NOT NULL DEFAULT 0"),
            ("usage_count", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("last_used_at", "TEXT"),
            ("send_failures", "INTEGER NOT NULL DEFAULT 0"),
        )
        for column, definition in sticker_migrations:
            if column not in sticker_columns:
                await db.execute(f"ALTER TABLE sticker_registry ADD COLUMN {column} {definition}")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sticker_user_usage (
                user_id INTEGER PRIMARY KEY,
                sticker_id INTEGER NOT NULL REFERENCES sticker_registry(id),
                last_used_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sticker_registry_unique_id
                ON sticker_registry(file_unique_id) WHERE file_unique_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_sticker_registry_category
                ON sticker_registry(category, approved);
            CREATE INDEX IF NOT EXISTS idx_sticker_registry_set
                ON sticker_registry(set_name);
            CREATE INDEX IF NOT EXISTS idx_sticker_registry_status
                ON sticker_registry(approved, id DESC);
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(12, ?)",
            (utc_now().isoformat(),),
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(13, ?)",
            (utc_now().isoformat(),),
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                task_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'planning','running','paused','completed','failed','cancelled','expired'
                )),
                payload BLOB NOT NULL,
                completed_steps INTEGER NOT NULL DEFAULT 0,
                step_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_missions_owner_created
                ON missions(owner_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_missions_state_updated
                ON missions(state, updated_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_missions_active_fingerprint
                ON missions(owner_id, task_fingerprint)
                WHERE state IN ('planning','running','paused');
            CREATE TABLE IF NOT EXISTS mission_steps (
                mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
                step_index INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','completed')),
                details BLOB NOT NULL,
                result BLOB,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(mission_id,step_index)
            );
            CREATE INDEX IF NOT EXISTS idx_mission_steps_state
                ON mission_steps(mission_id,state,step_index);

            CREATE TABLE IF NOT EXISTS evolution_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                patch_hash TEXT NOT NULL,
                source_hash TEXT NOT NULL DEFAULT '',
                payload BLOB NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'testing','awaiting_approval','approved_staging','rejected','cancelled',
                    'expired','staging_verified','awaiting_deployment_approval',
                    'deployment_approved','deployed','failed','rolled_back'
                )),
                test_passed INTEGER,
                test_report BLOB,
                test_exit_code INTEGER,
                test_timed_out INTEGER NOT NULL DEFAULT 0,
                reviewed_hash TEXT NOT NULL DEFAULT '',
                approver_id INTEGER,
                approved_at TEXT,
                deploy_approver_id INTEGER,
                deploy_approved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evolution_state_created
                ON evolution_proposals(state, created_at DESC);
            """
        )
        cursor = await db.execute("PRAGMA table_info(evolution_proposals)")
        evolution_columns = {str(row["name"]) for row in await cursor.fetchall()}
        await cursor.close()
        if "source_hash" not in evolution_columns:
            await db.execute(
                "ALTER TABLE evolution_proposals ADD COLUMN source_hash TEXT NOT NULL DEFAULT ''",
            )
        await db.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES(14, ?)",
            (utc_now().isoformat(),),
        )
        await db.commit()

    async def save_message(self, chat_id: int, role: str, content: str, keep: int = 12) -> None:
        db = self._db()
        await db.execute(
            "INSERT INTO messages(chat_id, role, content, created_at) VALUES(?,?,?,?)",
            (chat_id, role, content, utc_now().isoformat()),
        )
        await db.execute(
            "DELETE FROM messages WHERE chat_id=? AND id NOT IN "
            "(SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?)",
            (chat_id, chat_id, max(2, keep)),
        )
        await db.commit()

    async def history(self, chat_id: int, limit: int) -> list[dict[str, str]]:
        cursor = await self._db().execute(
            "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    async def save_group_message(
        self, chat_id: int, user_id: int, display_name: str, content: str, keep: int = 80,
    ) -> None:
        db = self._db()
        await db.execute(
            """
            INSERT INTO group_messages(chat_id, user_id, display_name, content, created_at)
            VALUES(?,?,?,?,?)
            """,
            (chat_id, user_id, display_name[:100], content[:2000], utc_now().isoformat()),
        )
        await db.execute(
            """
            DELETE FROM group_messages
            WHERE chat_id=? AND id NOT IN (
                SELECT id FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?
            )
            """,
            (chat_id, chat_id, max(50, min(keep, 100))),
        )
        await db.commit()

    async def recent_group_messages(self, chat_id: int, limit: int = 80) -> list[dict[str, str]]:
        cursor = await self._db().execute(
            """
            SELECT display_name, content, created_at
            FROM group_messages WHERE chat_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (chat_id, max(1, min(limit, 100))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"display_name": str(row["display_name"]), "content": str(row["content"]),
             "created_at": str(row["created_at"])}
            for row in reversed(rows)
        ]

    async def set_chat_mode(self, chat_id: int, mode: str) -> None:
        await self._db().execute(
            """
            INSERT INTO chat_settings(chat_id, mode, updated_at) VALUES(?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at
            """,
            (chat_id, mode, utc_now().isoformat()),
        )
        await self._db().commit()

    async def get_chat_mode(self, chat_id: int) -> str:
        cursor = await self._db().execute(
            "SELECT mode FROM chat_settings WHERE chat_id=?", (chat_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return str(row["mode"]) if row else "default"

    async def search_history(self, chat_id: int, query: str, limit: int = 10) -> list[dict[str, str]]:
        words = [word.strip(".,?!:;()[]{}\"'`") for word in query.split() if len(word) > 1][:8]
        if not words:
            return []
        clauses = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in words)
        patterns = [
            "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for word in words
        ]
        cursor = await self._db().execute(
            "SELECT role,content,created_at FROM messages WHERE chat_id=? AND (" + clauses + ") "
            "ORDER BY id DESC LIMIT ?",
            (chat_id, *patterns, max(1, min(limit, 20))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"role": str(row["role"]), "content": str(row["content"]),
             "created_at": str(row["created_at"])}
            for row in rows
        ]

    async def clear_history(self, chat_id: int) -> None:
        await self._db().execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        await self._db().commit()

    async def clear_user_conversation(self, user_id: int) -> None:
        """Clear private conversation history and long-term memory without touching shared chats."""
        user_id = int(user_id)
        if user_id <= 0:
            raise ValueError("A positive private-chat user ID is required")
        try:
            await self._db().executescript(
                "BEGIN IMMEDIATE;\n"
                f"DELETE FROM messages WHERE chat_id={user_id};\n"
                f"DELETE FROM memories WHERE user_id={user_id};\n"
                f"DELETE FROM user_memories WHERE user_id={user_id};\n"
                f"DELETE FROM learned_knowledge WHERE user_id={user_id} OR source_user_id={user_id};\n"
                "COMMIT;"
            )
        except Exception:
            await self._db().rollback()
            raise

    async def active_users(self, limit: int = 100) -> list[dict[str, Any]]:
        """List private-chat user IDs and activity metadata without returning chat content."""
        cursor = await self._db().execute(
            """
            WITH private_users AS (
                SELECT chat_id AS user_id FROM messages WHERE chat_id > 0
                UNION
                SELECT user_id FROM memories WHERE user_id > 0
            )
            SELECT users.user_id,
                   MAX(messages.created_at) AS last_active,
                   COUNT(messages.id) AS message_count
            FROM private_users AS users
            LEFT JOIN messages ON messages.chat_id=users.user_id
            GROUP BY users.user_id
            ORDER BY last_active DESC, users.user_id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def export_user_data(self, user_id: int, private_chat_id: int | None = None) -> dict[str, Any]:
        """Export user-owned records; chat-level content is included only for a private chat."""
        db = self._db()
        data: dict[str, Any] = {"user_id": user_id}

        async def rows(query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
            cursor = await db.execute(query, params)
            found = await cursor.fetchall()
            await cursor.close()
            return [dict(row) for row in found]

        data["preferences"] = await rows(
            "SELECT key,value,updated_at FROM user_preferences WHERE user_id=? ORDER BY key",
            (user_id,),
        )
        data["memories"] = await rows(
            "SELECT id,fact,created_at FROM memories WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        data["profile_facts"] = await rows(
            "SELECT key,value FROM user_memories WHERE user_id=? ORDER BY key",
            (user_id,),
        )
        data["learned_knowledge"] = await rows(
            "SELECT user_id,category,fact_key,fact_value,updated_at "
            "FROM learned_knowledge WHERE user_id=? OR source_user_id=? "
            "ORDER BY user_id,category,fact_key",
            (user_id, user_id),
        )
        data["reminders"] = await rows(
            "SELECT id,reminder_text,run_at,repeat_seconds,delivered,created_at "
            "FROM reminders WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        data["tasks"] = await rows(
            "SELECT id,task_text,status,priority,due_at,created_at,updated_at,"
            "(SELECT GROUP_CONCAT(depends_on_task_id) FROM task_dependencies "
            "WHERE task_id=user_tasks.id) AS dependency_ids FROM user_tasks "
            "WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        for task in data["tasks"]:
            raw_dependencies = task.pop("dependency_ids")
            task["depends_on"] = (
                [int(item) for item in raw_dependencies.split(",")] if raw_dependencies else []
            )
        data["study_cards"] = await rows(
            "SELECT id,question,answer,interval_days,due_at,created_at,reviewed_at "
            "FROM study_cards WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        data["usage"] = await rows(
            "SELECT model,input_tokens,output_tokens,cost_usd,created_at "
            "FROM usage_events WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        data["feedback"] = await rows(
            "SELECT rating,category,created_at FROM feedback WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        if self.cipher:
            mission_cursor = await db.execute(
                "SELECT * FROM missions WHERE owner_id=? ORDER BY created_at", (user_id,),
            )
            mission_rows = await mission_cursor.fetchall()
            await mission_cursor.close()
            data["missions"] = [
                {
                    "id": str(row["id"]), "state": str(row["state"]),
                    "completed_steps": int(row["completed_steps"]),
                    "step_count": int(row["step_count"]), "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "details": json.loads(self.cipher.decrypt_text(row["payload"])),
                }
                for row in mission_rows
            ]
            for mission in data["missions"]:
                record = await self.get_mission(str(mission["id"]), user_id)
                if record:
                    mission["details"] = record["payload"]
        else:
            data["missions"] = []
        proposal_cursor = await db.execute(
            "SELECT id,summary,patch_hash,source_hash,state,test_passed,created_at,expires_at,payload "
            "FROM evolution_proposals WHERE creator_id=? ORDER BY id", (user_id,),
        )
        proposal_rows = await proposal_cursor.fetchall()
        await proposal_cursor.close()
        data["evolution_proposals"] = []
        if self.cipher:
            for row in proposal_rows:
                payload = json.loads(self.cipher.decrypt_text(row["payload"]))
                patch_text = ""
                if isinstance(payload, dict) and payload.get("patch"):
                    try:
                        patch_text = base64.b64decode(payload["patch"], validate=True).decode("utf-8")
                    except (ValueError, UnicodeDecodeError):
                        patch_text = ""
                data["evolution_proposals"].append({
                    key: row[key] for key in (
                        "id", "summary", "patch_hash", "source_hash", "state",
                        "test_passed", "created_at", "expires_at",
                    )
                } | {
                    "summary": str(payload.get("summary", "")) if isinstance(payload, dict) else "",
                    "patch": patch_text,
                })
        data["sticker_usage"] = await rows(
            "SELECT r.category,r.emoji,u.last_used_at FROM sticker_user_usage u "
            "JOIN sticker_registry r ON r.id=u.sticker_id WHERE u.user_id=?",
            (user_id,),
        )
        data["agent_jobs"] = await rows(
            "SELECT task_type,provider,status,step_count,cost_usd,summary,created_at,updated_at "
            "FROM agent_jobs WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        data["knowledge_base"] = await self.list_kb_documents(user_id)
        for document in data["knowledge_base"]:
            document["chunks"] = await rows(
                "SELECT source,content FROM kb_chunks WHERE document_id=? ORDER BY id",
                (int(document["id"]),),
            )
            if self.cipher:
                for chunk in document["chunks"]:
                    chunk["source"] = self.cipher.decrypt_text(chunk["source"])
                    chunk["content"] = self.cipher.decrypt_text(chunk["content"])
        data["approvals"] = await rows(
            "SELECT kind,summary,required_approver,status,result,created_at,expires_at,payload "
            "FROM approvals WHERE requester_id=? ORDER BY id",
            (user_id,),
        )
        if self.cipher:
            for approval in data["approvals"]:
                approval["payload"] = json.loads(
                    self.cipher.decrypt(approval["payload"]).decode("utf-8")
                )
        else:
            for approval in data["approvals"]:
                approval.pop("payload", None)
        data["audit_events"] = await rows(
            "SELECT action,target,outcome,created_at FROM audit_events "
            "WHERE actor_id=? ORDER BY id",
            (user_id,),
        )
        moderation = await rows(
            "SELECT reason,created_at FROM blocked_users WHERE user_id=?",
            (user_id,),
        )
        data["moderation"] = moderation[0] if moderation else None
        if private_chat_id == user_id:
            data["private_chat_history"] = await rows(
                "SELECT role,content,created_at FROM messages WHERE chat_id=? ORDER BY id",
                (private_chat_id,),
            )
            data["temporary_documents"] = await rows(
                "SELECT id,filename,mime_type,created_at,expires_at FROM documents WHERE chat_id=? ORDER BY id",
                (private_chat_id,),
            )
            for document in data["temporary_documents"]:
                document["chunks"] = await rows(
                    "SELECT source,content FROM document_chunks WHERE document_id=? ORDER BY id",
                    (document["id"],),
                )
                document.pop("id")
        return data

    async def delete_user_data(self, user_id: int, private_chat_id: int | None = None) -> None:
        """Delete user-owned state atomically; shared group-chat history is never removed."""
        db = self._db()
        user_id = int(user_id)
        statements = [
            "BEGIN IMMEDIATE;",
            *(
                f"DELETE FROM {table} WHERE user_id={user_id};"
                for table in (
                    "user_preferences", "memories", "user_memories", "reminders", "user_tasks", "usage_events",
                    "study_cards", "feedback", "agent_jobs", "kb_documents",
                )
            ),
            f"DELETE FROM missions WHERE owner_id={user_id};",
            f"DELETE FROM evolution_proposals WHERE creator_id={user_id};",
            f"DELETE FROM learned_knowledge WHERE user_id={user_id} OR source_user_id={user_id};",
            f"DELETE FROM approvals WHERE requester_id={user_id};",
            f"DELETE FROM live_sessions WHERE user_id={user_id};",
            f"DELETE FROM audit_events WHERE actor_id={user_id};",
            f"DELETE FROM sticker_user_usage WHERE user_id={user_id};",
            f"UPDATE sticker_registry SET added_by=0 WHERE added_by={user_id};",
        ]
        if private_chat_id == user_id:
            chat_id = int(private_chat_id)
            statements.extend((
                f"DELETE FROM messages WHERE chat_id={chat_id};",
                f"DELETE FROM documents WHERE chat_id={chat_id};",
                f"DELETE FROM action_executions WHERE chat_id={chat_id};",
                f"DELETE FROM admin_reply_map WHERE user_chat_id={chat_id};",
            ))
        statements.append("COMMIT;")
        await db.executescript("\n".join(statements))

    async def set_user_blocked(self, user_id: int, blocked_by: int, reason: str = "") -> None:
        await self._db().execute(
            "INSERT INTO blocked_users(user_id,reason,blocked_by,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason,"
            "blocked_by=excluded.blocked_by,created_at=excluded.created_at",
            (user_id, reason[:240], blocked_by, utc_now().isoformat()),
        )
        await self._db().commit()
        await self.audit(blocked_by, "moderation.block", str(user_id), reason[:120])

    async def set_user_unblocked(self, user_id: int, unblocked_by: int) -> bool:
        cursor = await self._db().execute("DELETE FROM blocked_users WHERE user_id=?", (user_id,))
        changed = cursor.rowcount > 0
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(unblocked_by, "moderation.unblock", str(user_id), "unblocked")
        return changed

    async def is_user_blocked(self, user_id: int) -> bool:
        cursor = await self._db().execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def get_setting(self, key: str) -> str | None:
        cursor = await self._db().execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        await cursor.close()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._db().execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, utc_now().isoformat()),
        )
        await self._db().commit()

    async def set_live(self, user_id: int, active: bool) -> None:
        await self._db().execute(
            "INSERT INTO live_sessions(user_id,active,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET active=excluded.active, updated_at=excluded.updated_at",
            (user_id, int(active), utc_now().isoformat()),
        )
        await self._db().commit()

    async def is_live(self, user_id: int) -> bool:
        cursor = await self._db().execute(
            "SELECT active FROM live_sessions WHERE user_id=?", (user_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return bool(row["active"]) if row else False

    async def end_live(self, user_id: int) -> bool:
        cursor = await self._db().execute(
            "UPDATE live_sessions SET active=0,updated_at=? WHERE user_id=? AND active=1",
            (utc_now().isoformat(), user_id),
        )
        await self._db().commit()
        return cursor.rowcount > 0

    async def end_all_live(self) -> list[int]:
        cursor = await self._db().execute("SELECT user_id FROM live_sessions WHERE active=1")
        rows = await cursor.fetchall()
        await cursor.close()
        await self._db().execute(
            "UPDATE live_sessions SET active=0,updated_at=? WHERE active=1", (utc_now().isoformat(),)
        )
        await self._db().commit()
        return [int(row["user_id"]) for row in rows]

    async def active_live_count(self) -> int:
        cursor = await self._db().execute("SELECT COUNT(*) AS n FROM live_sessions WHERE active=1")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["n"])

    async def map_admin_message(
        self, message_id: int, user_chat_id: int, user_id: int = 0, theme_id: str = "",
    ) -> None:
        db = self._db()
        await db.execute(
            "INSERT OR REPLACE INTO admin_reply_map(admin_message_id,user_chat_id,user_id,theme_id,created_at) "
            "VALUES(?,?,?,?,?)",
            (message_id, user_chat_id, user_id, theme_id[:40], utc_now().isoformat()),
        )
        await db.execute(
            "DELETE FROM admin_reply_map WHERE admin_message_id NOT IN "
            "(SELECT admin_message_id FROM admin_reply_map ORDER BY created_at DESC LIMIT 10000)"
        )
        await db.commit()

    async def user_for_admin_message(self, message_id: int) -> int | None:
        cursor = await self._db().execute(
            "SELECT user_chat_id FROM admin_reply_map WHERE admin_message_id=?", (message_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["user_chat_id"]) if row else None

    async def admin_reply_context(self, message_id: int) -> dict[str, Any] | None:
        cursor = await self._db().execute(
            "SELECT user_chat_id,user_id,theme_id FROM admin_reply_map WHERE admin_message_id=?",
            (message_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if not row:
            return None
        return {
            "user_chat_id": int(row["user_chat_id"]),
            "user_id": int(row["user_id"]),
            "theme_id": str(row["theme_id"] or ""),
        }

    async def get_preference(self, user_id: int, key: str, default: str) -> str:
        cursor = await self._db().execute(
            "SELECT value FROM user_preferences WHERE user_id=? AND key=?", (user_id, key)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return str(row["value"]) if row else default

    async def set_preference(self, user_id: int, key: str, value: str) -> None:
        await self._db().execute(
            "INSERT INTO user_preferences(user_id,key,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (user_id, key, value, utc_now().isoformat()),
        )
        await self._db().commit()

    async def delete_preferences(
        self, user_id: int, *, prefix: str | None = None, keys: tuple[str, ...] = (),
    ) -> None:
        """Delete selected user preference keys without touching other profile data."""
        db = self._db()
        if prefix is not None:
            await db.execute(
                "DELETE FROM user_preferences WHERE user_id=? AND substr(key,1,?)=?",
                (user_id, len(prefix), prefix),
            )
        if keys:
            placeholders = ",".join("?" for _ in keys)
            await db.execute(
                f"DELETE FROM user_preferences WHERE user_id=? AND key IN ({placeholders})",
                (user_id, *keys),
            )
        await db.commit()

    async def add_memory(self, user_id: int, fact: str) -> int:
        cursor = await self._db().execute(
            "INSERT INTO memories(user_id,fact,created_at) VALUES(?,?,?)",
            (user_id, fact[:1000], utc_now().isoformat()),
        )
        await self._db().commit()
        await self._db().execute(
            "DELETE FROM memories WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM memories WHERE user_id=? ORDER BY id DESC LIMIT 30)",
            (user_id, user_id),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def memories(self, user_id: int) -> list[tuple[int, str]]:
        cursor = await self._db().execute(
            "SELECT id,fact FROM memories WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [(int(row["id"]), str(row["fact"])) for row in rows]

    async def save_user_fact(self, user_id: int, key: str, value: str) -> None:
        normalized_key = key.strip().casefold()
        normalized_value = value.strip()
        if user_id <= 0 or not normalized_key or len(normalized_key) > 64:
            raise ValueError("A positive user ID and a key of 1-64 characters are required")
        if not normalized_value or len(normalized_value) > 500:
            raise ValueError("A user fact value must contain 1-500 characters")
        db = self._db()
        await db.execute(
            """
            INSERT INTO user_memories(user_id, key, value) VALUES(?,?,?)
            ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value
            """,
            (user_id, normalized_key, normalized_value),
        )
        await db.execute(
            """
            DELETE FROM user_memories WHERE user_id=? AND key NOT IN (
                SELECT key FROM user_memories WHERE user_id=? ORDER BY rowid DESC LIMIT 50
            )
            """,
            (user_id, user_id),
        )
        await db.commit()

    async def get_user_facts(self, user_id: int) -> dict[str, str]:
        cursor = await self._db().execute(
            "SELECT key,value FROM user_memories WHERE user_id=? ORDER BY rowid DESC LIMIT 50",
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def save_learned_knowledge(
        self, user_id: int, category: str, fact_key: str, fact_value: str, *,
        source_user_id: int | None = None,
    ) -> None:
        category = category.strip().casefold()
        normalized_key = fact_key.strip().casefold()
        normalized_value = fact_value.strip()
        allowed_categories = {"user_preference", "fact", "entity", "general_knowledge"}
        if category not in allowed_categories:
            raise ValueError("Unsupported learned knowledge category")
        if user_id < 0 or (user_id == 0 and category != "general_knowledge"):
            raise ValueError("Only general knowledge may use the global scope")
        if user_id > 0 and category == "general_knowledge":
            raise ValueError("General knowledge must use the global scope")
        if source_user_id is not None and source_user_id <= 0:
            raise ValueError("A source user ID must be positive")
        if user_id == 0 and source_user_id is None:
            raise ValueError("Global knowledge must record its contributing user")
        if not normalized_key or len(normalized_key) > 64:
            raise ValueError("A fact key must contain 1-64 characters")
        if not normalized_value or len(normalized_value) > 500:
            raise ValueError("A learned fact must contain 1-500 characters")

        db = self._db()
        await db.execute(
            """
            INSERT INTO learned_knowledge(
                user_id, category, fact_key, fact_value, source_user_id, updated_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id, category, fact_key) DO UPDATE SET
                fact_value=excluded.fact_value,
                source_user_id=excluded.source_user_id,
                updated_at=excluded.updated_at
            """,
            (user_id, category, normalized_key, normalized_value, source_user_id, utc_now().isoformat()),
        )
        await db.execute(
            """
            DELETE FROM learned_knowledge WHERE user_id=? AND id NOT IN (
                SELECT id FROM learned_knowledge WHERE user_id=?
                ORDER BY updated_at DESC, id DESC LIMIT ?
            )
            """,
            (user_id, user_id, 200 if user_id == 0 else 100),
        )
        await db.commit()

    async def get_learned_knowledge(self, user_id: int, limit: int = 50) -> list[dict[str, str]]:
        if user_id <= 0:
            return []
        cursor = await self._db().execute(
            """
            SELECT category, fact_key, fact_value
            FROM learned_knowledge
            WHERE user_id IN (?, 0)
            ORDER BY CASE WHEN user_id=? THEN 0 ELSE 1 END, updated_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, user_id, max(1, min(limit, 100))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {
                "category": str(row["category"]),
                "key": str(row["fact_key"]),
                "value": str(row["fact_value"]),
            }
            for row in rows
        ]

    async def forget_memory(self, user_id: int, memory_id: int | None = None) -> int:
        if memory_id is None:
            db = self._db()
            cursor = await db.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
            facts = await db.execute("DELETE FROM user_memories WHERE user_id=?", (user_id,))
            learned = await db.execute(
                "DELETE FROM learned_knowledge WHERE user_id=? OR source_user_id=?",
                (user_id, user_id),
            )
            await db.commit()
            return cursor.rowcount + facts.rowcount + learned.rowcount
        else:
            cursor = await self._db().execute(
                "DELETE FROM memories WHERE user_id=? AND id=?", (user_id, memory_id)
            )
        await self._db().commit()
        return cursor.rowcount

    async def save_document(
        self, chat_id: int, filename: str, mime_type: str, chunks: list[tuple[str, str]]
    ) -> int:
        now = utc_now()
        expires = now + timedelta(hours=self.retention_hours)
        db = self._db()
        async with db.execute(
            "INSERT INTO documents(chat_id,filename,mime_type,created_at,expires_at) VALUES(?,?,?,?,?)",
            (chat_id, filename, mime_type, now.isoformat(), expires.isoformat()),
        ) as cursor:
            document_id = int(cursor.lastrowid or 0)
        await db.executemany(
            "INSERT INTO document_chunks(document_id,source,content) VALUES(?,?,?)",
            [(document_id, source, content) for source, content in chunks],
        )
        await db.commit()
        return document_id

    async def document_for_export(self, chat_id: int, document_id: int) -> dict[str, Any] | None:
        cursor = await self._db().execute(
            "SELECT filename,mime_type FROM documents WHERE id=? AND chat_id=? AND expires_at>?",
            (document_id, chat_id, utc_now().isoformat()),
        )
        document = await cursor.fetchone()
        await cursor.close()
        if document is None:
            return None
        cursor = await self._db().execute(
            "SELECT source,content FROM document_chunks WHERE document_id=? ORDER BY id",
            (document_id,),
        )
        chunks = await cursor.fetchall()
        await cursor.close()
        return {
            "filename": str(document["filename"]),
            "mime_type": str(document["mime_type"]),
            "chunks": [
                {"source": str(row["source"]), "content": str(row["content"])}
                for row in chunks
            ],
        }

    async def search_documents(self, chat_id: int, query: str, limit: int = 8) -> list[dict[str, str]]:
        words = [word.strip(".,?!:;()[]{}\"'") for word in query.split() if len(word) > 2]
        if not words:
            return []
        clauses = " OR ".join("c.content LIKE ?" for _ in words[:8])
        args: list[Any] = [f"%{word}%" for word in words[:8]]
        args.append(chat_id)
        args.append(utc_now().isoformat())
        args.append(limit)
        sql = (
            "SELECT d.filename,c.source,c.content FROM document_chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE (" + clauses + ") "
            "AND d.chat_id=? AND d.expires_at>? ORDER BY d.created_at DESC LIMIT ?"
        )
        cursor = await self._db().execute(sql, args)
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"filename": str(row["filename"]), "source": str(row["source"]), "content": str(row["content"])}
            for row in rows
        ]

    async def forget_documents(self, chat_id: int) -> int:
        cursor = await self._db().execute("DELETE FROM documents WHERE chat_id=?", (chat_id,))
        await self._db().commit()
        return cursor.rowcount

    async def promote_document_to_kb(self, user_id: int, chat_id: int, document_id: int) -> int:
        async with self._kb_lock:
            return await self._promote_document_to_kb(user_id, chat_id, document_id)

    async def _promote_document_to_kb(self, user_id: int, chat_id: int, document_id: int) -> int:
        if self.cipher is None:
            raise RuntimeError("Personal Knowledge Base encryption is not configured")
        cursor = await self._db().execute(
            "SELECT filename,mime_type FROM documents WHERE id=? AND chat_id=? AND expires_at>?",
            (document_id, chat_id, utc_now().isoformat()),
        )
        document = await cursor.fetchone()
        await cursor.close()
        if document is None:
            raise ValueError("Document ID is missing, expired, or belongs to another chat")
        cursor = await self._db().execute(
            "SELECT source,content FROM document_chunks WHERE document_id=? ORDER BY id",
            (document_id,),
        )
        chunks = await cursor.fetchall()
        await cursor.close()
        if not chunks:
            raise ValueError("This document has no locally extracted text to save")
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS documents,COALESCE(SUM(LENGTH(c.content)),0) AS bytes "
            "FROM kb_documents d LEFT JOIN kb_chunks c ON c.document_id=d.id WHERE d.user_id=?",
            (user_id,),
        )
        current = await cursor.fetchone()
        await cursor.close()
        if int(current["documents"]) >= self.kb_max_documents:
            raise ValueError(f"Personal Knowledge Base cap is {self.kb_max_documents} documents; delete one before saving more")
        encrypted_chunks = [
            (self.cipher.encrypt(str(row["source"])), self.cipher.encrypt(str(row["content"])))
            for row in chunks
        ]
        added_bytes = sum(len(source) + len(content) for source, content in encrypted_chunks)
        if int(current["bytes"]) + added_bytes > self.kb_max_bytes:
            raise ValueError(f"Personal Knowledge Base would exceed its {self.kb_max_bytes:,}-byte storage limit")
        db = self._db()
        async with db.execute(
            "INSERT INTO kb_documents(user_id,filename,mime_type,created_at) VALUES(?,?,?,?)",
            (user_id, self.cipher.encrypt(str(document["filename"])),
             self.cipher.encrypt(str(document["mime_type"])), utc_now().isoformat()),
        ) as insert:
            kb_id = int(insert.lastrowid or 0)
        await db.executemany(
            "INSERT INTO kb_chunks(document_id,source,content) VALUES(?,?,?)",
            [(kb_id, source, content) for source, content in encrypted_chunks],
        )
        await db.commit()
        await self.audit(user_id, "kb.save", str(kb_id), "encrypted")
        return kb_id

    async def list_kb_documents(self, user_id: int) -> list[dict[str, str]]:
        if self.cipher is None:
            return []
        cursor = await self._db().execute(
            "SELECT id,filename,mime_type,created_at FROM kb_documents WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [
            {"id": str(row["id"]), "filename": self.cipher.decrypt_text(row["filename"]),
             "mime_type": self.cipher.decrypt_text(row["mime_type"]), "created_at": str(row["created_at"])}
            for row in rows
        ]

    async def search_kb(self, user_id: int, query: str, limit: int = 5) -> list[dict[str, str]]:
        if self.cipher is None or not query.strip():
            return []
        cursor = await self._db().execute(
            "SELECT d.filename,c.source,c.content FROM kb_chunks c "
            "JOIN kb_documents d ON d.id=c.document_id WHERE d.user_id=?",
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        words = {word.casefold().strip(".,?!:;()[]{}\"'`") for word in query.split() if len(word) > 1}
        ranked: list[tuple[int, dict[str, str]]] = []
        for row in rows:
            content = self.cipher.decrypt_text(row["content"])
            folded = content.casefold()
            score = sum(folded.count(word) for word in words)
            if score:
                ranked.append((score, {
                    "filename": self.cipher.decrypt_text(row["filename"]),
                    "source": self.cipher.decrypt_text(row["source"]),
                    "content": content,
                }))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _score, item in ranked[:max(1, min(limit, 10))]]

    async def delete_kb_document(self, user_id: int, document_id: int | None = None) -> int:
        if document_id is None:
            cursor = await self._db().execute("DELETE FROM kb_documents WHERE user_id=?", (user_id,))
        else:
            cursor = await self._db().execute(
                "DELETE FROM kb_documents WHERE user_id=? AND id=?", (user_id, document_id)
            )
        await self._db().commit()
        await self.audit(user_id, "kb.delete", str(document_id or "all"), str(cursor.rowcount))
        return cursor.rowcount

    async def create_approval(
        self, requester_id: int, kind: str, summary: str, payload: dict[str, Any],
        required_approver: str, ttl_seconds: int = 300,
    ) -> int:
        if self.cipher is None:
            raise RuntimeError("Encrypted approval storage is unavailable")
        if required_approver not in {"requester", "admin"}:
            raise ValueError("Invalid approval policy")
        now = utc_now()
        expires = now + timedelta(seconds=max(30, min(ttl_seconds, 3600)))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cursor = await self._db().execute(
            "INSERT INTO approvals(requester_id,kind,summary,payload,required_approver,status,created_at,expires_at,updated_at) "
            "VALUES(?,?,?,?,?,'pending',?,?,?)",
            (requester_id, kind, summary[:240], self.cipher.encrypt(encoded), required_approver,
             now.isoformat(), expires.isoformat(), now.isoformat()),
        )
        await self._db().commit()
        approval_id = int(cursor.lastrowid or 0)
        await self.audit(requester_id, "approval.request", str(approval_id), kind)
        return approval_id

    async def pending_approvals(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT id,requester_id,kind,summary,required_approver,status,expires_at,created_at "
            "FROM approvals WHERE status='pending' AND expires_at>? ORDER BY id DESC LIMIT ?",
            (utc_now().isoformat(), max(1, min(limit, 250))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def consume_approval(
        self, approval_id: int, approver_id: int, admin_ids: frozenset[int],
    ) -> dict[str, Any] | None:
        if self.cipher is None:
            return None
        db = self._db()
        now = utc_now().isoformat()
        cursor = await db.execute(
            "UPDATE approvals SET status='executing',approver_id=?,updated_at=? "
            "WHERE id=? AND status='pending' AND expires_at>? AND "
            "((required_approver='admin' AND ?=1) OR "
            "(required_approver='requester' AND requester_id=?)) RETURNING *",
            (approver_id, now, approval_id, now, int(approver_id in admin_ids), approver_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        await db.commit()
        if not row:
            return None
        raw = self.cipher.decrypt(row["payload"])
        result = dict(row)
        result["payload"] = json.loads(raw.decode("utf-8"))
        await self.audit(approver_id, "approval.execute", str(approval_id), str(row["kind"]))
        return result

    async def finish_approval(self, approval_id: int, status: str, result: str) -> None:
        if status not in {"approved", "denied", "failed", "expired"}:
            raise ValueError("Invalid approval result state")
        cursor = await self._db().execute("SELECT approver_id,kind FROM approvals WHERE id=?", (approval_id,))
        row = await cursor.fetchone()
        await cursor.close()
        cursor = await self._db().execute(
            "UPDATE approvals SET status=?,result=?,payload=?,updated_at=? WHERE id=? AND status='executing'",
            (status, result[:240], self.cipher.encrypt("{}"), utc_now().isoformat(), approval_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if row and changed:
            await self.audit(int(row["approver_id"] or 0), "approval.result", str(approval_id), f"{row['kind']}:{status}")

    async def expire_approvals(self) -> int:
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "UPDATE approvals SET status='expired',result='approval expired',payload=?,updated_at=? "
            "WHERE status IN ('pending','executing') AND expires_at<=?",
            (self.cipher.encrypt("{}"), now, now),
        )
        count = cursor.rowcount
        await self._db().commit()
        if count:
            await self.audit(0, "approval.expire", "pending", str(count))
        return count

    async def deny_approval(self, approval_id: int, actor_id: int, admin_ids: frozenset[int]) -> bool:
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "UPDATE approvals SET status='denied',approver_id=?,result='declined by authorized approver',payload=?,updated_at=? "
            "WHERE id=? AND status='pending' AND expires_at>? AND "
            "((required_approver='admin' AND ?=1) OR "
            "(required_approver='requester' AND requester_id=?))",
            (actor_id, self.cipher.encrypt("{}"), now, approval_id, now, int(actor_id in admin_ids), actor_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(actor_id, "approval.deny", str(approval_id), "authorized approver")
        return changed

    async def create_agent_job(self, user_id: int, task_type: str) -> int:
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "INSERT INTO agent_jobs(user_id,task_type,status,created_at,updated_at) VALUES(?,?,'running',?,?)",
            (user_id, task_type[:40], now, now),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def finish_agent_job(
        self, job_id: int, status: str, provider: str, steps: int, cost_usd: float, summary: str,
    ) -> None:
        await self._db().execute(
            "UPDATE agent_jobs SET status=?,provider=?,step_count=?,cost_usd=?,summary=?,updated_at=? WHERE id=?",
            (status[:24], provider[:100], max(0, steps), _usage_float(cost_usd), summary[:500], utc_now().isoformat(), job_id),
        )
        await self._db().commit()

    async def recent_agent_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT id,user_id,task_type,provider,status,step_count,cost_usd,summary,created_at,updated_at "
            "FROM agent_jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 250)),)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def create_mission(
        self, owner_id: int, chat_id: int, task: str, payload: dict[str, Any],
        ttl_seconds: int = 30 * 86400,
    ) -> tuple[str, bool]:
        if self.cipher is None:
            raise RuntimeError("Encrypted mission storage is unavailable")
        normalized = " ".join(task.casefold().split())
        if owner_id <= 0 or chat_id <= 0 or not normalized or len(task) > 4000:
            raise ValueError("Mission details are invalid")
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "SELECT id FROM missions WHERE owner_id=? AND task_fingerprint=? "
                "AND state IN ('planning','running','paused') LIMIT 1",
                (owner_id, fingerprint),
            )
            duplicate = await cursor.fetchone()
            await cursor.close()
            if duplicate:
                await db.commit()
                return str(duplicate["id"]), False
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM missions WHERE owner_id=? "
                "AND state IN ('planning','running','paused')",
                (owner_id,),
            )
            active = await cursor.fetchone()
            await cursor.close()
            if int(active["n"]) >= 3:
                raise ValueError("You already have three active missions")
            now = utc_now()
            mission_id = uuid.uuid4().hex[:12].upper()
            encrypted = self.cipher.encrypt(json.dumps(payload, ensure_ascii=False))
            await db.execute(
                "INSERT INTO missions(id,owner_id,chat_id,task_fingerprint,state,payload,created_at,updated_at,expires_at) "
                "VALUES(?,?,?,?, 'planning', ?,?,?,?)",
                (mission_id, owner_id, chat_id, fingerprint, encrypted,
                 now.isoformat(), now.isoformat(), (now + timedelta(seconds=max(60, ttl_seconds))).isoformat()),
            )
            await db.commit()
            await self.audit(owner_id, "mission.create", mission_id, "planning")
            return mission_id, True
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    def _mission_row(row: sqlite3.Row, cipher: DataCipher) -> dict[str, Any]:
        item = dict(row)
        try:
            payload = json.loads(cipher.decrypt_text(item.pop("payload")))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Stored mission data is unavailable") from exc
        item["payload"] = payload if isinstance(payload, dict) else {}
        return item

    async def get_mission(self, mission_id: str, owner_id: int | None = None) -> dict[str, Any] | None:
        if self.cipher is None:
            raise RuntimeError("Encrypted mission storage is unavailable")
        query = "SELECT * FROM missions WHERE id=?"
        params: tuple[Any, ...] = (mission_id.upper(),)
        if owner_id is not None:
            query += " AND owner_id=?"
            params += (owner_id,)
        cursor = await self._db().execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        if not row:
            return None
        item = self._mission_row(row, self.cipher)
        steps_cursor = await self._db().execute(
            "SELECT step_index,state,details,result FROM mission_steps WHERE mission_id=? ORDER BY step_index",
            (mission_id.upper(),),
        )
        step_rows = await steps_cursor.fetchall()
        await steps_cursor.close()
        if step_rows:
            details: list[dict[str, Any]] = []
            states: list[str] = []
            results: list[dict[str, str] | None] = []
            for step_row in step_rows:
                try:
                    step = json.loads(self.cipher.decrypt_text(step_row["details"]))
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("Stored mission step is unavailable") from exc
                step = step if isinstance(step, dict) else {}
                details.append(step)
                states.append(str(step_row["state"]))
                if step_row["result"] is None:
                    results.append(None)
                else:
                    try:
                        results.append({
                            "title": str(step.get("title", "Analysis")),
                            "text": self.cipher.decrypt_text(step_row["result"]),
                        })
                    except ValueError as exc:
                        raise RuntimeError("Stored mission result is unavailable") from exc
            item["payload"]["steps"] = details
            item["payload"]["step_states"] = states
            item["payload"]["results"] = results
        return item

    async def list_missions(
        self, owner_id: int | None = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if self.cipher is None:
            raise RuntimeError("Encrypted mission storage is unavailable")
        query = "SELECT * FROM missions"
        params: tuple[Any, ...] = ()
        if owner_id is not None:
            query += " WHERE owner_id=?"
            params = (owner_id,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params += (max(1, min(limit, 50)),)
        cursor = await self._db().execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._mission_row(row, self.cipher) for row in rows]

    async def update_mission(
        self, mission_id: str, *, expected: set[str], state: str | None = None,
        payload: dict[str, Any] | None = None, completed_steps: int | None = None,
        step_count: int | None = None, error_code: str | None = None,
    ) -> bool:
        allowed = {
            "planning": {"running", "paused", "failed", "cancelled", "expired"},
            "running": {"paused", "completed", "failed", "cancelled", "expired"},
            "paused": {"running", "cancelled", "expired"},
            "completed": set(), "failed": set(), "cancelled": set(), "expired": set(),
        }
        if not expected or any(item not in allowed for item in expected):
            raise ValueError("Invalid mission transition source")
        if state is not None and any(state != current and state not in allowed[current] for current in expected):
            raise ValueError("Invalid mission state transition")
        if self.cipher is None:
            raise RuntimeError("Encrypted mission storage is unavailable")
        assignments = ["updated_at=?"]
        values: list[Any] = [utc_now().isoformat()]
        if state is not None:
            assignments.append("state=?")
            values.append(state)
        if payload is not None:
            assignments.append("payload=?")
            values.append(self.cipher.encrypt(json.dumps(payload, ensure_ascii=False)))
        if completed_steps is not None:
            assignments.append("completed_steps=?")
            values.append(max(0, min(completed_steps, 12)))
        if step_count is not None:
            assignments.append("step_count=?")
            values.append(max(0, min(step_count, 12)))
        if error_code is not None:
            assignments.append("error_code=?")
            values.append(error_code[:80])
        placeholders = ",".join("?" for _ in expected)
        values.extend([mission_id.upper(), *sorted(expected)])
        cursor = await self._db().execute(
            f"UPDATE missions SET {','.join(assignments)} WHERE id=? AND state IN ({placeholders})",
            values,
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed and state in {"completed", "failed", "cancelled", "expired"}:
            await self.audit(0, "mission.finish", mission_id.upper(), state)
        return changed

    async def initialize_mission_steps(self, mission_id: str, steps: list[dict[str, Any]]) -> None:
        if self.cipher is None:
            raise RuntimeError("Encrypted mission storage is unavailable")
        if not 1 <= len(steps) <= 12:
            raise ValueError("Mission step plan is outside its configured limit")
        now = utc_now().isoformat()
        for index, step in enumerate(steps):
            await self._db().execute(
                "INSERT OR IGNORE INTO mission_steps(mission_id,step_index,state,details,updated_at) "
                "VALUES(?,?,'pending',?,?)",
                (mission_id.upper(), index, self.cipher.encrypt(json.dumps(step, ensure_ascii=False)), now),
            )
        await self._db().execute(
            "UPDATE missions SET step_count=?,updated_at=? WHERE id=?",
            (len(steps), now, mission_id.upper()),
        )
        await self._db().commit()

    async def record_mission_step(
        self, mission_id: str, step_index: int, result_text: str,
    ) -> tuple[bool, str, int, int]:
        """Persist a single completed step without rewriting concurrent sibling results."""
        if self.cipher is None:
            raise RuntimeError("Encrypted mission storage is unavailable")
        db = self._db()
        cursor = await db.execute(
            "SELECT state,completed_steps,step_count FROM missions WHERE id=?",
            (mission_id.upper(),),
        )
        mission = await cursor.fetchone()
        await cursor.close()
        if not mission:
            return False, "missing", 0, 0
        state = str(mission["state"])
        completed_before, step_count = int(mission["completed_steps"]), int(mission["step_count"])
        if state not in {"running", "paused"}:
            return False, state, completed_before, step_count
        cursor = await db.execute(
            "SELECT state,details FROM mission_steps WHERE mission_id=? AND step_index=?",
            (mission_id.upper(), step_index),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if not row or str(row["state"]) != "pending":
            return False, state, completed_before, step_count
        details = json.loads(self.cipher.decrypt_text(row["details"]))
        dependencies = details.get("depends_on", []) if isinstance(details, dict) else []
        if not isinstance(dependencies, list):
            return False, state, completed_before, step_count
        for dependency in dependencies:
            if isinstance(dependency, bool) or not isinstance(dependency, int) or not 0 <= dependency < step_index:
                return False, state, completed_before, step_count
            cursor = await db.execute(
                "SELECT state FROM mission_steps WHERE mission_id=? AND step_index=?",
                (mission_id.upper(), dependency),
            )
            dependency_row = await cursor.fetchone()
            await cursor.close()
            if not dependency_row or str(dependency_row["state"]) != "completed":
                return False, state, completed_before, step_count
        now = utc_now().isoformat()
        cursor = await db.execute(
            "UPDATE mission_steps SET state='completed',result=?,updated_at=? "
            "WHERE mission_id=? AND step_index=? AND state='pending' AND EXISTS ("
            "SELECT 1 FROM missions WHERE id=? AND state IN ('running','paused'))",
            (self.cipher.encrypt(result_text[:8000]), now, mission_id.upper(), step_index, mission_id.upper()),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        if not changed:
            await db.commit()
            return False, state, completed_before, step_count
        await db.commit()
        await db.execute(
            "UPDATE missions SET completed_steps=(SELECT COUNT(*) FROM mission_steps "
            "WHERE mission_id=? AND state='completed'),updated_at=? WHERE id=?",
            (mission_id.upper(), utc_now().isoformat(), mission_id.upper()),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT completed_steps FROM missions WHERE id=?", (mission_id.upper(),),
        )
        count_row = await cursor.fetchone()
        await cursor.close()
        completed = int(count_row["completed_steps"]) if count_row else completed_before
        return True, state, completed, step_count

    async def recover_missions_after_restart(self) -> int:
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "UPDATE missions SET state='paused',updated_at=? WHERE state IN ('planning','running')",
            (now,),
        )
        count = cursor.rowcount
        await cursor.close()
        await self._db().commit()
        if count:
            await self.audit(0, "mission.recovery", "active", f"paused:{count}")
        return count

    async def expire_missions(self) -> int:
        now = utc_now().isoformat()
        expired = await self._db().execute_fetchall(
            "SELECT id FROM missions WHERE state!='expired' AND expires_at<=?", (now,),
        )
        for row in expired:
            await self._db().execute("DELETE FROM mission_steps WHERE mission_id=?", (str(row["id"]),))
        cursor = await self._db().execute(
            "UPDATE missions SET state='expired',payload=?,updated_at=? "
            "WHERE state!='expired' AND expires_at<=?",
            (self.cipher.encrypt("{}") if self.cipher else b"{}", now, now),
        )
        count = cursor.rowcount
        await cursor.close()
        await self._db().commit()
        return count

    async def cleanup_mission_history(self, retention_days: int = 90) -> int:
        cutoff = (utc_now() - timedelta(days=max(30, min(retention_days, 3650)))).isoformat()
        cursor = await self._db().execute(
            "DELETE FROM missions WHERE state IN ('completed','failed','cancelled','expired') AND updated_at<?",
            (cutoff,),
        )
        count = cursor.rowcount
        await cursor.close()
        await self._db().commit()
        return count

    async def delete_mission(self, mission_id: str, owner_id: int) -> bool:
        cursor = await self._db().execute(
            "DELETE FROM missions WHERE id=? AND owner_id=?",
            (mission_id.upper(), owner_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(owner_id, "mission.delete", mission_id.upper(), "owner request")
        return changed

    async def create_evolution_proposal(
        self, creator_id: int, summary: str, patch: bytes, archive: bytes, *,
        ttl_seconds: int = 7 * 86400,
    ) -> int:
        if self.cipher is None:
            raise RuntimeError("Encrypted evolution storage is unavailable")
        if (
            creator_id <= 0 or not summary.strip() or len(summary) > 500
            or not patch or len(patch) > 1_000_000 or not archive or len(archive) > 20 * 1024 * 1024
        ):
            raise ValueError("Evolution proposal details are invalid")
        patch_hash = hashlib.sha256(patch).hexdigest()
        source_hash = hashlib.sha256(archive).hexdigest()
        payload = json.dumps({
            "patch": base64.b64encode(patch).decode("ascii"),
            "archive": base64.b64encode(archive).decode("ascii"),
            "summary": " ".join(summary.split())[:500],
        }, separators=(",", ":"))
        now = utc_now()
        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM evolution_proposals WHERE expires_at>? "
                "AND state IN ('testing','awaiting_approval','approved_staging','staging_verified',"
                "'awaiting_deployment_approval','deployment_approved')",
                (now.isoformat(),),
            )
            total = await cursor.fetchone()
            await cursor.close()
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM evolution_proposals WHERE creator_id=? AND expires_at>? "
                "AND state IN ('testing','awaiting_approval','approved_staging','staging_verified',"
                "'awaiting_deployment_approval','deployment_approved')",
                (creator_id, now.isoformat()),
            )
            personal = await cursor.fetchone()
            await cursor.close()
            if int(total["n"]) >= 10 or int(personal["n"]) >= 3:
                raise ValueError("Evolution proposal retention limit reached")
            cursor = await db.execute(
                "INSERT INTO evolution_proposals(creator_id,summary,patch_hash,source_hash,payload,state,created_at,updated_at,expires_at) "
                "VALUES(?,?,?,?,?,'testing',?,?,?)",
                (creator_id, "AI-generated code change proposal", patch_hash, source_hash, self.cipher.encrypt(payload),
                 now.isoformat(), now.isoformat(), (now + timedelta(seconds=max(60, ttl_seconds))).isoformat()),
            )
            proposal_id = int(cursor.lastrowid or 0)
            await cursor.close()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        await self.audit(creator_id, "evolution.create", str(proposal_id), patch_hash[:16])
        return proposal_id

    async def get_evolution_proposal(self, proposal_id: int) -> dict[str, Any] | None:
        cursor = await self._db().execute(
            "SELECT * FROM evolution_proposals WHERE id=?", (proposal_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if not row:
            return None
        item = dict(row)
        if self.cipher is None:
            raise RuntimeError("Encrypted evolution storage is unavailable")
        payload = json.loads(self.cipher.decrypt_text(item["payload"]))
        item["payload"] = payload if isinstance(payload, dict) else {}
        item["summary"] = str(item["payload"].get("summary", item.get("summary", "")))
        if item.get("test_report") is not None:
            item["test_report"] = self.cipher.decrypt_text(item["test_report"])
        return item

    async def list_evolution_proposals(
        self, limit: int = 20, *, pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        where = "WHERE state IN ('testing','awaiting_approval','approved_staging','staging_verified','awaiting_deployment_approval','deployment_approved')" if pending_only else ""
        cursor = await self._db().execute(
            "SELECT id,creator_id,summary,patch_hash,source_hash,state,test_passed,test_exit_code,test_timed_out,"
            "reviewed_hash,approver_id,approved_at,deploy_approver_id,deploy_approved_at,created_at,updated_at,expires_at "
            f"FROM evolution_proposals {where} ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 50)),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def record_evolution_test(
        self, proposal_id: int, patch_hash: str, report: str, exit_code: int, timed_out: bool,
    ) -> bool:
        if self.cipher is None:
            raise RuntimeError("Encrypted evolution storage is unavailable")
        passed = exit_code == 0 and not timed_out
        state = "awaiting_approval" if passed else "failed"
        cursor = await self._db().execute(
            "UPDATE evolution_proposals SET state=?,test_passed=?,test_report=?,test_exit_code=?,"
            "test_timed_out=?,updated_at=? WHERE id=? AND state='testing' AND patch_hash=? AND expires_at>?",
            (state, int(passed), self.cipher.encrypt(report[:24000]), exit_code, int(timed_out),
             utc_now().isoformat(), proposal_id, patch_hash, utc_now().isoformat()),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(0, "evolution.test", str(proposal_id), "passed" if passed else "failed")
        return changed

    async def decide_evolution_staging(
        self, proposal_id: int, patch_hash: str, approver_id: int,
        admin_ids: frozenset[int], *, approve: bool,
    ) -> bool:
        if approver_id not in admin_ids:
            return False
        now = utc_now().isoformat()
        if approve:
            sql = (
                "UPDATE evolution_proposals SET state='approved_staging',reviewed_hash=?,approver_id=?,"
                "approved_at=?,updated_at=? WHERE id=? AND state='awaiting_approval' AND test_passed=1 "
                "AND patch_hash=? AND expires_at>?"
            )
            params: tuple[Any, ...] = (patch_hash, approver_id, now, now, proposal_id, patch_hash, now)
        else:
            sql = (
                "UPDATE evolution_proposals SET state='rejected',summary='',reviewed_hash=?,approver_id=?,"
                "approved_at=?,payload=?,test_report=NULL,updated_at=? WHERE id=? AND state IN ('testing','awaiting_approval') "
                "AND patch_hash=? AND expires_at>?"
            )
            params = (patch_hash, approver_id, now, self.cipher.encrypt("{}") if self.cipher else b"{}",
                      now, proposal_id, patch_hash, now)
        cursor = await self._db().execute(sql, params)
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(approver_id, "evolution.stage_decision", str(proposal_id), "approved" if approve else "rejected")
        return changed

    async def record_evolution_staging_verification(
        self, proposal_id: int, patch_hash: str, approver_id: int, admin_ids: frozenset[int],
        report: str, exit_code: int, timed_out: bool,
    ) -> bool:
        if approver_id not in admin_ids or self.cipher is None:
            return False
        passed = exit_code == 0 and not timed_out
        state = "awaiting_deployment_approval" if passed else "failed"
        cursor = await self._db().execute(
            "UPDATE evolution_proposals SET state=?,test_report=?,test_exit_code=?,test_timed_out=?,updated_at=? "
            "WHERE id=? AND state='approved_staging' AND reviewed_hash=? AND patch_hash=? AND expires_at>?",
            (state, self.cipher.encrypt(report[:24000]), exit_code, int(timed_out), utc_now().isoformat(),
             proposal_id, patch_hash, patch_hash, utc_now().isoformat()),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(approver_id, "evolution.staging_verify", str(proposal_id), "passed" if passed else "failed")
        return changed

    async def approve_evolution_deployment(
        self, proposal_id: int, patch_hash: str, approver_id: int, admin_ids: frozenset[int],
    ) -> bool:
        if approver_id not in admin_ids:
            return False
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "UPDATE evolution_proposals SET state='deployment_approved',deploy_approver_id=?,"
            "deploy_approved_at=?,updated_at=? WHERE id=? AND state='awaiting_deployment_approval' "
            "AND reviewed_hash=? AND patch_hash=? AND expires_at>?",
            (approver_id, now, now, proposal_id, patch_hash, patch_hash, now),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(approver_id, "evolution.deploy_approval", str(proposal_id), patch_hash[:16])
        return changed

    async def cancel_evolution_proposal(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: frozenset[int],
    ) -> bool:
        if actor_id not in admin_ids or self.cipher is None:
            return False
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "UPDATE evolution_proposals SET state='cancelled',summary='',payload=?,test_report=NULL,updated_at=? "
            "WHERE id=? AND patch_hash=? AND state IN ('testing','awaiting_approval','approved_staging',"
            "'staging_verified','awaiting_deployment_approval','deployment_approved') AND expires_at>?",
            (self.cipher.encrypt("{}"), now, proposal_id, patch_hash, now),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        if changed:
            await self.audit(actor_id, "evolution.cancel", str(proposal_id), patch_hash[:16])
        return changed

    async def expire_evolution_proposals(self) -> int:
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "UPDATE evolution_proposals SET state='expired',summary='',payload=?,test_report=NULL,updated_at=? "
            "WHERE state IN ('testing','awaiting_approval','approved_staging','staging_verified',"
            "'awaiting_deployment_approval','deployment_approved') AND expires_at<=?",
            (self.cipher.encrypt("{}") if self.cipher else b"{}", now, now),
        )
        count = cursor.rowcount
        await cursor.close()
        await self._db().commit()
        return count

    async def cleanup_evolution_history(self, retention_days: int = 90) -> int:
        cutoff = (utc_now() - timedelta(days=max(30, min(retention_days, 3650)))).isoformat()
        cursor = await self._db().execute(
            "DELETE FROM evolution_proposals WHERE state IN ('rejected','cancelled','expired','failed',"
            "'deployment_approved','deployed','rolled_back') AND updated_at<?",
            (cutoff,),
        )
        count = cursor.rowcount
        await cursor.close()
        await self._db().commit()
        return count

    async def record_feedback(self, user_id: int, rating: int, category: str = "") -> None:
        if rating not in {-1, 1}:
            raise ValueError("Feedback rating must be -1 or 1")
        normalized = " ".join("".join(char for char in category if char.isprintable()).split()).casefold()
        aliases = {
            "accuracy": "accuracy", "wrong answer": "accuracy", "incorrect": "accuracy",
            "language": "language", "translation": "language", "roman bangla": "language",
            "code": "code", "coding": "code", "bug": "code",
            "provider": "provider", "api": "provider", "fallback": "provider",
            "latency": "latency", "slow": "latency",
            "format": "format", "formatting": "format",
            "tool": "tool", "privacy": "privacy", "other": "other",
        }
        category = aliases.get(normalized, "other" if normalized else "")
        await self._db().execute(
            "INSERT INTO feedback(user_id,rating,category,created_at) VALUES(?,?,?,?)",
            (user_id, rating, category[:40], utc_now().isoformat()),
        )
        await self._db().commit()
        await self.audit(user_id, "feedback.record", category[:40], str(rating))

    async def feedback_suggestions(self, limit: int = 6) -> list[dict[str, Any]]:
        since = (utc_now() - timedelta(days=30)).isoformat()
        cursor = await self._db().execute(
            "SELECT LOWER(TRIM(category)) AS category,COUNT(*) AS negative_count "
            "FROM feedback WHERE rating=-1 AND category<>'' AND created_at>=? "
            "GROUP BY LOWER(TRIM(category)) HAVING COUNT(*)>=2 "
            "ORDER BY negative_count DESC LIMIT ?",
            (since, max(1, min(limit, 12))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [{"category": str(row["category"])[:40], "negative_count": int(row["negative_count"]),
                 "suggestion": f"Review recent {str(row['category'])[:40]} feedback and consider a manual quality improvement."}
                for row in rows]

    async def audit(self, actor_id: int, action: str, target: str = "", outcome: str = "") -> None:
        await self._db().execute(
            "INSERT INTO audit_events(actor_id,action,target,outcome,created_at) VALUES(?,?,?,?,?)",
            (actor_id, action[:80], target[:120], outcome[:120], utc_now().isoformat()),
        )
        await self._db().commit()

    async def claim_action_execution(
        self, chat_id: int, message_id: int, action_key: str, action_name: str,
        actor_id: int | None = None,
    ) -> bool:
        """Atomically allow one execution of a normalized action per Telegram message."""
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "INSERT OR IGNORE INTO action_executions(chat_id,message_id,action_key,action_name,status,created_at,updated_at) "
            "VALUES(?,?,?,?, 'running', ?, ?)",
            (chat_id, message_id, action_key, action_name[:80], now, now),
        )
        await self._db().commit()
        claimed = cursor.rowcount == 1
        await cursor.close()
        if claimed:
            await self.audit(actor_id or chat_id, "tool.action", action_name[:80], "claimed")
        return claimed

    async def finish_action_execution(
        self, chat_id: int, message_id: int, action_key: str, status: str, result: str,
    ) -> None:
        if status not in {"complete", "failed"}:
            raise ValueError("Invalid action execution status")
        await self._db().execute(
            "UPDATE action_executions SET status=?, result=?, updated_at=? "
            "WHERE chat_id=? AND message_id=? AND action_key=? AND status='running'",
            (status, result[:500], utc_now().isoformat(), chat_id, message_id, action_key),
        )
        await self._db().commit()

    async def cleanup_action_executions(self, retention_days: int = 90) -> int:
        cutoff = (utc_now() - timedelta(days=max(1, min(retention_days, 3650)))).isoformat()
        cursor = await self._db().execute(
            "DELETE FROM action_executions WHERE created_at < ?", (cutoff,)
        )
        await self._db().commit()
        count = cursor.rowcount
        await cursor.close()
        return max(0, count)

    async def dashboard_metrics(self) -> dict[str, Any]:
        today = utc_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS requests,COALESCE(SUM(cost_usd),0) AS cost FROM usage_events WHERE created_at>=?", (today,)
        )
        usage = await cursor.fetchone()
        await cursor.close()
        cursor = await self._db().execute("SELECT COUNT(*) AS n FROM approvals WHERE status='pending' AND expires_at>?", (utc_now().isoformat(),))
        pending = await cursor.fetchone()
        await cursor.close()
        cursor = await self._db().execute("SELECT COUNT(*) AS n FROM agent_jobs WHERE status='running'")
        running = await cursor.fetchone()
        await cursor.close()
        cursor = await self._db().execute("SELECT COUNT(*) AS n FROM feedback WHERE created_at>=? AND rating=1", (today,))
        positive = await cursor.fetchone()
        await cursor.close()
        cursor = await self._db().execute("SELECT COUNT(*) AS n FROM feedback WHERE created_at>=? AND rating=-1", (today,))
        negative = await cursor.fetchone()
        await cursor.close()
        return {
            "requests_today": int(usage["requests"]), "cost_today_usd": float(usage["cost"]),
            "pending_approvals": int(pending["n"]), "running_agents": int(running["n"]),
            "feedback_positive_today": int(positive["n"]), "feedback_negative_today": int(negative["n"]),
        }

    async def recent_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT actor_id,action,target,outcome,created_at FROM audit_events ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 250)),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def list_documents(self, chat_id: int) -> list[dict[str, str]]:
        cursor = await self._db().execute(
            "SELECT id,filename,mime_type,expires_at FROM documents WHERE chat_id=? AND expires_at>? ORDER BY created_at DESC",
            (chat_id, utc_now().isoformat()),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def save_sticker(
        self, *, file_id: str, file_unique_id: str | None, emoji: str = "",
        set_name: str = "", set_title: str = "", sticker_type: str = "regular",
        is_animated: bool = False, is_video: bool = False, width: int = 0,
        height: int = 0, category: str = "general", tags: list[str] | None = None,
        source_type: str = "manual", added_by: int = 0, approved: bool = False,
    ) -> bool:
        if not file_id.strip() or not category.strip():
            raise ValueError("Sticker file ID and category are required")
        file_unique_id = file_unique_id.strip() or None if file_unique_id else None
        now = utc_now().isoformat()
        async with self._sticker_lock:
            db = self._db()
            cursor = await db.execute(
                "SELECT 1 FROM sticker_registry WHERE "
                "(? IS NOT NULL AND file_unique_id=?) OR file_id=? LIMIT 1",
                (file_unique_id, file_unique_id, file_id),
            )
            duplicate = await cursor.fetchone()
            await cursor.close()
            if duplicate:
                return False
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO sticker_registry(
                    file_id,file_unique_id,category,emoji,label,added_by,added_at,
                    set_name,set_title,sticker_type,is_animated,is_video,width,height,
                    tags,source_type,approved,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    file_id.strip(), file_unique_id, category.strip()[:40], emoji[:32],
                    set_title[:120] or set_name[:120], added_by, now, set_name[:120],
                    set_title[:120], sticker_type[:40], int(is_animated), int(is_video),
                    max(0, int(width)), max(0, int(height)),
                    json.dumps(_normalize_sticker_tags(tags), ensure_ascii=False),
                    source_type[:24], int(approved), now,
                ),
            )
            inserted = cursor.rowcount == 1
            await cursor.close()
            await db.commit()
            return inserted

    async def list_stickers(
        self, category: str | None = None, *, approved_only: bool = False,
        pending_only: bool = False, limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category=?")
            params.append(category)
        if approved_only:
            clauses.extend(["approved=1", "send_failures<3"])
        if pending_only:
            clauses.append("approved=0")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self._db().execute(
            "SELECT * FROM sticker_registry" + where + " ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(limit, 2000))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_sticker_row(row) for row in rows]

    async def search_stickers(
        self, query: str, *, approved_only: bool = False, limit: int = 50,
    ) -> list[dict[str, Any]]:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        match = f"%{escaped}%"
        matches = [
            "category LIKE ? ESCAPE '\\'",
            "emoji LIKE ? ESCAPE '\\'",
            "set_name LIKE ? ESCAPE '\\'",
            "set_title LIKE ? ESCAPE '\\'",
            "label LIKE ? ESCAPE '\\'",
            "tags LIKE ? ESCAPE '\\'",
        ]
        params: list[Any] = [match] * len(matches)
        if query:
            matches.extend(["file_unique_id=?", "file_id=?"])
            params.extend([query, query])
        clauses = ["(" + " OR ".join(matches) + ")"]
        if approved_only:
            clauses.extend(["approved=1", "send_failures<3"])
        cursor = await self._db().execute(
            "SELECT * FROM sticker_registry WHERE " + " AND ".join(clauses) +
            " ORDER BY usage_count DESC,id DESC LIMIT ?",
            (*params, max(1, min(limit, 200))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_sticker_row(row) for row in rows]

    async def update_sticker_category(
        self, sticker_id: int, category: str, tags: list[str] | None = None,
    ) -> bool:
        if not category.strip():
            raise ValueError("Sticker category is required")
        cursor = await self._db().execute(
            "UPDATE sticker_registry SET category=?,tags=?,approved=1 WHERE id=?",
            (
                category.strip()[:40],
                json.dumps(_normalize_sticker_tags(tags), ensure_ascii=False),
                sticker_id,
            ),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def sticker_stats(self) -> dict[str, int]:
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS total,COALESCE(SUM(approved),0) AS approved,"
            "SUM(CASE WHEN approved=0 THEN 1 ELSE 0 END) AS pending,"
            "SUM(CASE WHEN is_animated=1 THEN 1 ELSE 0 END) AS animated,"
            "SUM(CASE WHEN is_video=1 THEN 1 ELSE 0 END) AS video,"
            "COALESCE(SUM(usage_count),0) AS usage_count FROM sticker_registry",
        )
        row = await cursor.fetchone()
        await cursor.close()
        result = {key: int(row[key] or 0) for key in (
            "total", "approved", "pending", "animated", "video", "usage_count",
        )}
        cursor = await self._db().execute(
            "SELECT COUNT(DISTINCT set_name) AS packs FROM sticker_registry WHERE set_name<>''",
        )
        packs = await cursor.fetchone()
        await cursor.close()
        result["packs"] = int(packs["packs"])
        return result

    async def record_sticker_usage(self, sticker_id: int, user_id: int | None = None) -> None:
        db = self._db()
        now = utc_now().isoformat()
        await db.execute(
            "UPDATE sticker_registry SET usage_count=usage_count+1,last_used_at=? WHERE id=?",
            (now, sticker_id),
        )
        if user_id is not None:
            await db.execute(
                "INSERT INTO sticker_user_usage(user_id,sticker_id,last_used_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET sticker_id=excluded.sticker_id,"
                "last_used_at=excluded.last_used_at",
                (user_id, sticker_id, now),
            )
        await db.commit()

    async def last_user_sticker_use(self, user_id: int) -> str | None:
        cursor = await self._db().execute(
            "SELECT last_used_at FROM sticker_user_usage WHERE user_id=?",
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return str(row["last_used_at"]) if row else None

    async def record_sticker_send_failure(self, sticker_id: int) -> None:
        await self._db().execute(
            "UPDATE sticker_registry SET send_failures=send_failures+1 WHERE id=?",
            (sticker_id,),
        )
        await self._db().commit()

    async def export_sticker_metadata(self) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT id,file_id,file_unique_id,emoji,set_name,set_title,sticker_type,"
            "is_animated,is_video,width,height,category,tags,source_type,approved,"
            "usage_count,created_at,last_used_at FROM sticker_registry ORDER BY id",
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_sticker_row(row) for row in rows]

    async def cleanup_documents(self) -> int:
        cursor = await self._db().execute("DELETE FROM documents WHERE expires_at<=?", (utc_now().isoformat(),))
        await self._db().commit()
        return cursor.rowcount

    async def add_reminder(
        self, user_id: int, chat_id: int, text: str, run_at: datetime,
        repeat_seconds: int | None = None,
    ) -> int:
        if repeat_seconds is not None and repeat_seconds < 60:
            raise ValueError("Repeating reminders must be at least one minute apart")
        cursor = await self._db().execute("SELECT COUNT(*) AS n FROM reminders WHERE user_id=? AND delivered=0", (user_id,))
        current = await cursor.fetchone()
        await cursor.close()
        if int(current["n"]) >= self.max_pending_reminders:
            raise ValueError("User reminder limit reached")
        cursor = await self._db().execute(
            "INSERT INTO reminders(user_id,chat_id,reminder_text,run_at,created_at,repeat_seconds) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, chat_id, text[:500], run_at.astimezone(UTC).isoformat(),
             utc_now().isoformat(), repeat_seconds),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def due_reminders(self) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT id,user_id,chat_id,reminder_text,repeat_seconds,attempts FROM reminders "
            "WHERE delivered=0 AND paused=0 AND run_at<=? "
            "AND (claimed_until IS NULL OR claimed_until<=?)",
            (utc_now().isoformat(), utc_now().isoformat()),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def claim_due_reminders(self, limit: int = 100, lease_seconds: int = 300) -> list[dict[str, Any]]:
        now = utc_now()
        now_text = now.isoformat()
        lease_until = (now + timedelta(seconds=max(30, lease_seconds))).isoformat()
        cursor = await self._db().execute(
            "UPDATE reminders SET claimed_until=?,attempts=attempts+1 "
            "WHERE id IN (SELECT id FROM reminders WHERE delivered=0 AND paused=0 AND run_at<=? "
            "AND (claimed_until IS NULL OR claimed_until<=?) ORDER BY run_at,id LIMIT ?) "
            "RETURNING id,user_id,chat_id,reminder_text,repeat_seconds,attempts",
            (lease_until, now_text, now_text, max(1, min(limit, 500))),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        await self._db().commit()
        return [dict(row) for row in rows]

    async def defer_reminder(self, reminder_id: int, retry_after_seconds: int) -> bool:
        delay = max(30, min(retry_after_seconds, 86400))
        retry_at = (utc_now() + timedelta(seconds=delay)).isoformat()
        cursor = await self._db().execute(
            "UPDATE reminders SET claimed_until=? WHERE id=? AND delivered=0 AND paused=0",
            (retry_at, reminder_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def mark_reminder_delivered(self, reminder_id: int) -> None:
        db = self._db()
        cursor = await db.execute(
            "SELECT run_at,repeat_seconds FROM reminders WHERE id=? AND delivered=0",
            (reminder_id,),
        )
        reminder = await cursor.fetchone()
        await cursor.close()
        if reminder is None:
            return
        interval = reminder["repeat_seconds"]
        if interval is None:
            await db.execute(
                "UPDATE reminders SET delivered=1,claimed_until=NULL,attempts=0 WHERE id=?",
                (reminder_id,),
            )
        else:
            next_run = datetime.fromisoformat(str(reminder["run_at"]))
            now = utc_now()
            interval_delta = timedelta(seconds=int(interval))
            if next_run <= now:
                missed = int((now - next_run) // interval_delta) + 1
                next_run += missed * interval_delta
            await db.execute(
                "UPDATE reminders SET run_at=?,claimed_until=NULL,attempts=0 WHERE id=? AND delivered=0",
                (next_run.isoformat(), reminder_id),
            )
        await db.commit()

    async def list_reminders(self, user_id: int) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT id,reminder_text,run_at,repeat_seconds,paused FROM reminders "
            "WHERE user_id=? AND delivered=0 ORDER BY paused,run_at",
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def cancel_reminder(self, user_id: int, reminder_id: int) -> bool:
        cursor = await self._db().execute(
            "DELETE FROM reminders WHERE id=? AND user_id=? AND delivered=0", (reminder_id, user_id)
        )
        await self._db().commit()
        return cursor.rowcount > 0

    async def set_reminder_paused(self, user_id: int, reminder_id: int, paused: bool) -> bool:
        cursor = await self._db().execute(
            "UPDATE reminders SET paused=? WHERE id=? AND user_id=? AND delivered=0",
            (int(paused), reminder_id, user_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def snooze_reminder(self, user_id: int, reminder_id: int, delay_seconds: int) -> bool:
        if not 60 <= delay_seconds <= 365 * 86400:
            raise ValueError("Snooze must be between one minute and 365 days")
        cursor = await self._db().execute(
            "UPDATE reminders SET run_at=?,claimed_until=NULL WHERE id=? AND user_id=? AND delivered=0",
            ((utc_now() + timedelta(seconds=delay_seconds)).isoformat(), reminder_id, user_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def add_task(
        self, user_id: int, text: str, priority: str = "normal", due_at: str | None = None,
        depends_on: list[int] | None = None,
    ) -> int:
        text = text.strip()
        if user_id <= 0 or not text or len(text) > 500:
            raise ValueError("Task text must contain 1-500 characters")
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ValueError("Invalid task priority")
        if due_at is not None:
            try:
                date.fromisoformat(due_at)
            except ValueError as exc:
                raise ValueError("Task deadline must be an ISO date") from exc
        dependencies = list(dict.fromkeys(depends_on or []))
        if len(dependencies) > 10 or any(
            isinstance(task_id, bool) or not isinstance(task_id, int) or task_id <= 0
            for task_id in dependencies
        ):
            raise ValueError("A task may depend on at most 10 valid task IDs")

        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM user_tasks WHERE user_id=? AND status='open'",
                (user_id,),
            )
            current = await cursor.fetchone()
            await cursor.close()
            if int(current["n"]) >= 100:
                raise ValueError("Open task limit reached")

            if dependencies:
                placeholders = ",".join("?" for _ in dependencies)
                cursor = await db.execute(
                    f"SELECT id FROM user_tasks WHERE user_id=? AND id IN ({placeholders})",
                    (user_id, *dependencies),
                )
                found = {int(row["id"]) for row in await cursor.fetchall()}
                await cursor.close()
                if found != set(dependencies):
                    raise ValueError("Every prerequisite must be an existing task owned by you")

            now = utc_now().isoformat()
            cursor = await db.execute(
                "INSERT INTO user_tasks(user_id,task_text,priority,due_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, text, priority, due_at, now, now),
            )
            task_id = int(cursor.lastrowid or 0)
            await cursor.close()
            await db.executemany(
                "INSERT INTO task_dependencies(task_id,depends_on_task_id) VALUES(?,?)",
                [(task_id, dependency_id) for dependency_id in dependencies],
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return task_id

    async def add_task_plan(self, user_id: int, steps: list[dict[str, Any]]) -> list[int]:
        if user_id <= 0 or not isinstance(steps, list) or not 1 <= len(steps) <= 10:
            raise ValueError("A task plan must contain 1-10 steps")
        validated: list[tuple[str, str, str | None, list[int]]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError("Every task plan step must be an object")
            text = step.get("text")
            priority = step.get("priority", "normal")
            due_at = step.get("due_at")
            dependencies = step.get("depends_on", [])
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 500:
                raise ValueError("Each task plan step needs 1-500 characters of text")
            if not isinstance(priority, str) or priority not in {"low", "normal", "high", "urgent"}:
                raise ValueError("Invalid task plan priority")
            if due_at is not None:
                if not isinstance(due_at, str):
                    raise ValueError("Invalid task plan deadline")
                try:
                    date.fromisoformat(due_at)
                except ValueError as exc:
                    raise ValueError("Task deadline must be an ISO date") from exc
            if not isinstance(dependencies, list) or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 1 or item >= index
                for item in dependencies
            ):
                raise ValueError("Task plan prerequisites must refer to earlier step numbers")
            validated.append(
                (text.strip(), priority, due_at, list(dict.fromkeys(dependencies))),
            )

        db = self._db()
        await db.execute("BEGIN IMMEDIATE")
        task_ids: list[int] = []
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM user_tasks WHERE user_id=? AND status='open'",
                (user_id,),
            )
            current = await cursor.fetchone()
            await cursor.close()
            if int(current["n"]) + len(validated) > 100:
                raise ValueError("Open task limit reached")
            now = utc_now().isoformat()
            for index, (text, priority, due_at, dependencies) in enumerate(validated, start=1):
                cursor = await db.execute(
                    "INSERT INTO user_tasks(user_id,task_text,priority,due_at,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (user_id, text, priority, due_at, now, now),
                )
                task_id = int(cursor.lastrowid or 0)
                await cursor.close()
                task_ids.append(task_id)
                await db.executemany(
                    "INSERT INTO task_dependencies(task_id,depends_on_task_id) VALUES(?,?)",
                    [(task_id, task_ids[dependency - 1]) for dependency in dependencies],
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return task_ids

    async def list_tasks(self, user_id: int) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT task.id,task.task_text,task.status,task.priority,task.due_at,"
            "task.created_at,task.updated_at,"
            "(SELECT GROUP_CONCAT(depends_on_task_id) FROM task_dependencies "
            "WHERE task_id=task.id) AS dependency_ids,"
            "(SELECT GROUP_CONCAT(dependency.depends_on_task_id) FROM task_dependencies AS dependency "
            "JOIN user_tasks AS prerequisite ON prerequisite.id=dependency.depends_on_task_id "
            "WHERE dependency.task_id=task.id AND prerequisite.status!='done') AS blocked_dependency_ids "
            "FROM user_tasks AS task "
            "WHERE user_id=? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,"
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,"
            "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,id DESC LIMIT 100",
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        tasks = []
        for row in rows:
            task = dict(row)
            raw_dependencies = task.pop("dependency_ids")
            task["depends_on"] = (
                [int(item) for item in raw_dependencies.split(",")] if raw_dependencies else []
            )
            raw_blockers = task.pop("blocked_dependency_ids")
            task["blocked_by"] = (
                [int(item) for item in raw_blockers.split(",")] if raw_blockers else []
            )
            task["blocked_count"] = len(task["blocked_by"])
            tasks.append(task)
        return tasks

    async def complete_task(self, user_id: int, task_id: int) -> bool:
        cursor = await self._db().execute(
            "UPDATE user_tasks SET status='done',updated_at=? "
            "WHERE id=? AND user_id=? AND status='open' AND NOT EXISTS ("
            "SELECT 1 FROM task_dependencies AS dependency "
            "JOIN user_tasks AS prerequisite ON prerequisite.id=dependency.depends_on_task_id "
            "WHERE dependency.task_id=user_tasks.id AND prerequisite.status!='done')",
            (utc_now().isoformat(), task_id, user_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def delete_task(self, user_id: int, task_id: int) -> bool:
        cursor = await self._db().execute(
            "SELECT 1 FROM user_tasks WHERE id=? AND user_id=? AND EXISTS ("
            "SELECT 1 FROM task_dependencies AS dependency "
            "JOIN user_tasks AS dependent ON dependent.id=dependency.task_id "
            "WHERE dependency.depends_on_task_id=user_tasks.id "
            "AND dependent.user_id=user_tasks.user_id AND dependent.status='open')",
            (task_id, user_id),
        )
        has_open_dependents = await cursor.fetchone() is not None
        await cursor.close()
        if has_open_dependents:
            raise ValueError("Open tasks depend on this task")
        cursor = await self._db().execute(
            "DELETE FROM user_tasks WHERE id=? AND user_id=?",
            (task_id, user_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def add_study_card(self, user_id: int, question: str, answer: str) -> int:
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS n FROM study_cards WHERE user_id=?", (user_id,),
        )
        current = await cursor.fetchone()
        await cursor.close()
        if int(current["n"]) >= 500:
            raise ValueError("Study card limit reached")
        cursor = await self._db().execute(
            "INSERT INTO study_cards(user_id,question,answer,due_at,created_at) "
            "VALUES(?,?,?,?,?)",
            (user_id, question[:500], answer[:1000], now, now),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def list_study_cards(
        self, user_id: int, *, due_only: bool = False, limit: int = 30,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id,question,answer,interval_days,due_at,created_at,reviewed_at "
            "FROM study_cards WHERE user_id=?"
        )
        params: tuple[Any, ...] = (user_id,)
        if due_only:
            query += " AND due_at<=?"
            params += (utc_now().isoformat(),)
        query += " ORDER BY due_at,id LIMIT ?"
        params += (max(1, min(limit, 100)),)
        cursor = await self._db().execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def review_study_card(self, user_id: int, card_id: int, *, remembered: bool) -> bool:
        cursor = await self._db().execute(
            "SELECT interval_days FROM study_cards WHERE id=? AND user_id=?",
            (card_id, user_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return False
        current = int(row["interval_days"])
        interval = min(180, max(1, current * 2 if current else 1)) if remembered else 0
        now = utc_now()
        due_at = (now + timedelta(days=interval)).isoformat()
        await self._db().execute(
            "UPDATE study_cards SET interval_days=?,due_at=?,reviewed_at=? "
            "WHERE id=? AND user_id=?",
            (interval, due_at, now.isoformat(), card_id, user_id),
        )
        await self._db().commit()
        return True

    async def delete_study_card(self, user_id: int, card_id: int) -> bool:
        cursor = await self._db().execute(
            "DELETE FROM study_cards WHERE id=? AND user_id=?", (card_id, user_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def record_usage(
        self, user_id: int, model: str, usage: dict[str, Any]
    ) -> None:
        prompt = _usage_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _usage_int(usage.get("completion_tokens") or usage.get("output_tokens"))
        cost = _usage_float(usage.get("cost"))
        await self._db().execute(
            "INSERT INTO usage_events(user_id,model,input_tokens,output_tokens,cost_usd,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, model, prompt, completion, max(0, cost), utc_now().isoformat()),
        )
        await self._db().commit()

    async def begin_usage(self, user_id: int, model: str, estimated_cost: float = 0.0) -> int:
        cursor = await self._db().execute(
            "INSERT INTO usage_events(user_id,model,cost_usd,created_at) VALUES(?,?,?,?)",
            (user_id, model[:200], max(0.0, estimated_cost), utc_now().isoformat()),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def reserve_usage(
        self, user_id: int, model: str, estimated_cost: float,
        user_request_limit: int, global_daily_cost_limit: float,
        user_monthly_request_limit: int = 1200, user_monthly_cost_limit: float = 50.0,
    ) -> tuple[int | None, str | None]:
        """Atomically reserve one request and its estimated cost against daily caps."""
        start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        estimate = max(0.0, estimated_cost)
        async with self._usage_lock:
            db = self._db()
            cursor = await db.execute(
                "SELECT COUNT(*) AS requests,COALESCE(SUM(cost_usd),0) AS cost "
                "FROM usage_events WHERE user_id=? AND created_at>=?",
                (user_id, start),
            )
            user_today = await cursor.fetchone()
            await cursor.close()
            if int(user_today["requests"]) >= user_request_limit:
                return None, "request_limit"
            cursor = await db.execute(
                "SELECT COUNT(*) AS requests,COALESCE(SUM(cost_usd),0) AS cost "
                "FROM usage_events WHERE user_id=? AND created_at>=?",
                (user_id, month_start),
            )
            user_month = await cursor.fetchone()
            await cursor.close()
            if int(user_month["requests"]) >= user_monthly_request_limit:
                return None, "monthly_request_limit"
            if float(user_month["cost"]) + estimate > user_monthly_cost_limit:
                return None, "monthly_cost_limit"
            cursor = await db.execute(
                "SELECT COALESCE(SUM(cost_usd),0) AS cost FROM usage_events WHERE created_at>=?", (start,)
            )
            global_today = await cursor.fetchone()
            await cursor.close()
            if float(global_today["cost"]) + estimate > global_daily_cost_limit:
                return None, "cost_limit"
            cursor = await db.execute(
                "INSERT INTO usage_events(user_id,model,cost_usd,created_at) VALUES(?,?,?,?)",
                (user_id, model[:200], estimate, utc_now().isoformat()),
            )
            await db.commit()
            return int(cursor.lastrowid or 0), None

    async def finish_usage(self, event_id: int, model: str, usage: dict[str, Any]) -> None:
        prompt = _usage_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _usage_int(usage.get("completion_tokens") or usage.get("output_tokens"))
        actual_cost = _usage_float(usage.get("cost"))
        await self._db().execute(
            "UPDATE usage_events SET model=?,input_tokens=?,output_tokens=?,cost_usd=MAX(cost_usd,?) WHERE id=?",
            (model[:200], prompt, completion, actual_cost, event_id),
        )
        await self._db().commit()

    async def usage_today(self, user_id: int) -> dict[str, float | int]:
        start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        month_start = utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS requests,COALESCE(SUM(cost_usd),0) AS cost "
            "FROM usage_events WHERE user_id=? AND created_at>=?",
            (user_id, start),
        )
        row = await cursor.fetchone()
        await cursor.close()
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS requests,COALESCE(SUM(cost_usd),0) AS cost "
            "FROM usage_events WHERE user_id=? AND created_at>=?",
            (user_id, month_start),
        )
        month = await cursor.fetchone()
        await cursor.close()
        return {
            "requests": int(row["requests"]), "cost": float(row["cost"]),
            "monthly_requests": int(month["requests"]), "monthly_cost": float(month["cost"]),
        }

    async def record_provider_file(self, file_id: str) -> None:
        await self.set_setting(f"provider_file:{file_id}", "pending_cleanup")

    async def pending_provider_files(self) -> list[str]:
        cursor = await self._db().execute("SELECT key FROM settings WHERE key LIKE 'provider_file:%'")
        rows = await cursor.fetchall()
        await cursor.close()
        return [str(row["key"]).split(":", 1)[1] for row in rows]

    async def clear_provider_file(self, file_id: str) -> None:
        await self._db().execute("DELETE FROM settings WHERE key=?", (f"provider_file:{file_id}",))
        await self._db().commit()
