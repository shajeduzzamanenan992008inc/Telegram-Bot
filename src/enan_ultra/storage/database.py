"""Asynchronous SQLite persistence: schema migrations, privacy scoping and audit chaining.

Every write runs on one dedicated executor thread so that:
    * a single SQLite connection serialises writers without "cannot start a
      transaction within a transaction" failures,
    * a caller that is cancelled never leaves a half-open transaction behind,
    * public methods can call each other (``set_global_access_mode`` records an
      audit event) without deadlocking on the writer.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from enan_ultra.crypto import DataCipher

CHAT_MODES: tuple[str, ...] = (
    "default", "sarcastic", "anime", "professional", "regional",
)
TASK_PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
MISSION_STATES: tuple[str, ...] = (
    "planning", "running", "paused", "completed", "cancelled", "failed",
)
ACTIVE_MISSION_STATES: tuple[str, ...] = ("planning", "running", "paused")
TERMINAL_MISSION_STATES: tuple[str, ...] = ("completed", "cancelled", "failed")
EVOLUTION_STATES: tuple[str, ...] = (
    "testing", "tested", "test_failed", "approved_staging", "staging_failed",
    "staging_verified", "deployment_approved", "cancelled",
)
TERMINAL_EVOLUTION_STATES: tuple[str, ...] = (
    "staging_verified", "deployment_approved", "cancelled", "test_failed", "staging_failed",
)
APPROVAL_STATUSES: tuple[str, ...] = ("pending", "approved", "denied", "failed", "expired")
STICKER_CATEGORIES: tuple[str, ...] = (
    "greeting", "happy", "celebration", "thanks", "thinking", "confused",
    "encouragement", "supportive", "programming", "science", "goodbye", "general",
)

_MILESTONES: tuple[tuple[int, str], ...] = (
    (1, """
        CREATE TABLE IF NOT EXISTS processed_updates (
            update_id INTEGER PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_processed_updates_time
            ON processed_updates(processed_at);
    """),
    (2, """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user','assistant')),
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
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_group_messages_chat_id
            ON group_messages(chat_id, id DESC);
    """),
    (3, """
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL CHECK (mode IN (
                'default','sarcastic','anime','professional','regional'
            )),
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS live_sessions (
            user_id INTEGER PRIMARY KEY,
            active INTEGER NOT NULL CHECK (active IN (0,1)),
            started_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_reply_map (
            message_id INTEGER PRIMARY KEY,
            user_chat_id INTEGER NOT NULL,
            user_id INTEGER,
            theme_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """),
    (4, """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        );
    """),
    (5, """
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
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id, key);
        CREATE TABLE IF NOT EXISTS learned_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            fact_key TEXT NOT NULL,
            fact_value TEXT NOT NULL,
            source_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (user_id, category, fact_key)
        );
        CREATE INDEX IF NOT EXISTS idx_learned_knowledge_scope
            ON learned_knowledge(user_id, category, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_learned_knowledge_source
            ON learned_knowledge(source_user_id);
    """),
    (6, """
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
            content TEXT NOT NULL,
            chunk_index INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_doc_chunks_document ON document_chunks(document_id);
    """),
    (7, """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            reminder_text TEXT NOT NULL,
            run_at TEXT NOT NULL,
            run_at_epoch REAL NOT NULL,
            repeat_seconds INTEGER,
            delivered INTEGER NOT NULL DEFAULT 0 CHECK (delivered IN (0,1)),
            paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1)),
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_until REAL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(delivered, run_at);
    """),
    (8, """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            estimated_cost REAL NOT NULL DEFAULT 0,
            actual_cost REAL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'reserved',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_user_created
            ON usage_events(user_id, created_at DESC);
    """),
    (9, """
        CREATE TABLE IF NOT EXISTS approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            summary TEXT NOT NULL,
            payload BLOB,
            required_approver TEXT NOT NULL CHECK (
                required_approver IN ('admin','requester')
            ),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                'pending','approved','denied','failed','expired'
            )),
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            resolved_at TEXT,
            resolved_by INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_approvals_status_expiry
            ON approvals(status, expires_at);
    """),
    (10, """
        CREATE TABLE IF NOT EXISTS agent_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            models TEXT NOT NULL DEFAULT '',
            step_count INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_jobs_created ON agent_jobs(created_at DESC);
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK (rating IN (-1,0,1)),
            category TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);
    """),
    (11, """
        CREATE TABLE IF NOT EXISTS kb_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename BLOB NOT NULL,
            mime_type BLOB NOT NULL,
            byte_size INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_documents_user ON kb_documents(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
            source BLOB NOT NULL,
            content BLOB NOT NULL,
            chunk_index INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);
    """),
    (12, """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
    """),
    (13, """
        CREATE TABLE IF NOT EXISTS action_executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            action_key TEXT NOT NULL,
            action_name TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running','complete','failed')),
            result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            finished_at TEXT,
            UNIQUE (chat_id, message_id, action_key)
        );
        CREATE INDEX IF NOT EXISTS idx_action_executions_created
            ON action_executions(created_at);
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id INTEGER PRIMARY KEY,
            blocked_by INTEGER,
            reason TEXT NOT NULL DEFAULT '',
            blocked_at TEXT NOT NULL
        );
    """),
    (14, """
        CREATE TABLE IF NOT EXISTS user_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
            priority TEXT NOT NULL DEFAULT 'normal' CHECK (
                priority IN ('low','normal','high','urgent')
            ),
            due_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_user_tasks_open
            ON user_tasks(user_id, status, id DESC);
        CREATE TABLE IF NOT EXISTS task_dependencies (
            task_id INTEGER NOT NULL REFERENCES user_tasks(id) ON DELETE CASCADE,
            depends_on_task_id INTEGER NOT NULL REFERENCES user_tasks(id) ON DELETE CASCADE,
            PRIMARY KEY (task_id, depends_on_task_id),
            CHECK (task_id <> depends_on_task_id)
        );
        CREATE INDEX IF NOT EXISTS idx_task_dependencies_prerequisite
            ON task_dependencies(depends_on_task_id, task_id);
        CREATE TABLE IF NOT EXISTS study_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            due_at TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 1,
            repetitions INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_study_cards_due
            ON study_cards(user_id, due_at, id);
    """),
    (15, """
        CREATE TABLE IF NOT EXISTS sticker_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL UNIQUE,
            file_unique_id TEXT,
            emoji TEXT NOT NULL DEFAULT '',
            set_name TEXT NOT NULL DEFAULT '',
            set_title TEXT NOT NULL DEFAULT '',
            sticker_type TEXT NOT NULL DEFAULT 'regular',
            is_animated INTEGER NOT NULL DEFAULT 0,
            is_video INTEGER NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT 'general',
            tags TEXT NOT NULL DEFAULT '[]',
            source_type TEXT NOT NULL DEFAULT 'pack_import',
            added_by INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0 CHECK (approved IN (0,1)),
            usage_count INTEGER NOT NULL DEFAULT 0,
            send_failures INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            added_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sticker_registry_unique_id
            ON sticker_registry(file_unique_id) WHERE file_unique_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_sticker_registry_category
            ON sticker_registry(category, approved);
        CREATE INDEX IF NOT EXISTS idx_sticker_registry_set
            ON sticker_registry(set_name);
        CREATE INDEX IF NOT EXISTS idx_sticker_registry_status
            ON sticker_registry(approved, id DESC);
        CREATE TABLE IF NOT EXISTS sticker_user_usage (
            user_id INTEGER PRIMARY KEY,
            sticker_id INTEGER NOT NULL REFERENCES sticker_registry(id),
            last_used_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS update_upload_gate (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL CHECK (state IN ('disabled','open','processing')),
            opened_by INTEGER,
            expires_at REAL,
            token TEXT,
            update_key TEXT,
            updated_at TEXT NOT NULL
        );
    """),
    (16, """
        CREATE TABLE IF NOT EXISTS missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            task_text TEXT NOT NULL,
            task_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'planning' CHECK (state IN (
                'planning','running','paused','completed','cancelled','failed'
            )),
            payload BLOB,
            error_code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_missions_owner_created
            ON missions(owner_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_missions_state_updated
            ON missions(state, updated_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_missions_active_fingerprint
            ON missions(owner_id, task_fingerprint)
            WHERE state IN ('planning','running','paused');
        CREATE TABLE IF NOT EXISTS mission_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mission_id INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
            step_index INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
                'pending','running','completed','failed','paused'
            )),
            title BLOB,
            result BLOB,
            updated_at TEXT NOT NULL,
            UNIQUE (mission_id, step_index)
        );
        CREATE INDEX IF NOT EXISTS idx_mission_steps_state
            ON mission_steps(mission_id, state, step_index);
        CREATE TABLE IF NOT EXISTS evolution_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            summary TEXT NOT NULL,
            patch_hash TEXT NOT NULL,
            source_hash TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'testing' CHECK (state IN (
                'testing','tested','test_failed','approved_staging',
                'staging_failed','staging_verified','deployment_approved','cancelled'
            )),
            patch BLOB,
            archive BLOB,
            test_report BLOB,
            test_exit_code INTEGER,
            test_passed INTEGER,
            test_timed_out INTEGER NOT NULL DEFAULT 0,
            staging_report BLOB,
            candidate_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_evolution_state_created
            ON evolution_proposals(state, created_at DESC);
    """),
)


class Database:
    """Single-writer, WAL-enabled SQLite service for all assistant state."""

    def __init__(
        self,
        path: str | Path,
        retention_hours: int = 24,
        max_pending_reminders: int = 20,
        data_key: str = "",
        kb_max_documents: int = 50,
        kb_max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.path = Path(path)
        self.document_retention_hours = max(1, int(retention_hours))
        self.max_pending_reminders = max(1, int(max_pending_reminders))
        self.kb_max_documents = max(1, int(kb_max_documents))
        self.kb_max_bytes = max(1, int(kb_max_bytes))
        self._cipher: DataCipher | None = DataCipher(data_key) if len(data_key) >= 32 else None
        self._connection: sqlite3.Connection | None = None
        self._reader: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._writer_ident: int | None = None
        self._closed = True

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _day_start(now: datetime) -> str:
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    @staticmethod
    def _month_start(now: datetime) -> str:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    def _wrap(self, value: str | None) -> bytes | None:
        """Encrypt user-controlled content when APP_DATA_KEY is configured."""
        if value is None:
            return None
        if self._cipher is None:
            return value.encode("utf-8")
        return self._cipher.encrypt(value)

    def _unwrap(self, value: Any) -> str:
        if value is None:
            return ""
        raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
        if self._cipher is not None:
            try:
                return self._cipher.decrypt_text(raw)
            except ValueError:
                return raw.decode("utf-8", "replace")
        return raw.decode("utf-8", "replace")

    @staticmethod
    def _like(term: str) -> str:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @contextmanager
    def _transaction(self) -> Iterable[sqlite3.Connection]:
        connection = self._require_connection()
        if connection.in_transaction:  # join the caller's transaction
            yield connection
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        connection.commit()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("The database is not open")
        return self._connection

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        connection = self._require_connection()
        cursor = connection.execute(sql, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        connection = self._require_connection()
        cursor = connection.execute(sql, params)
        try:
            row = cursor.fetchone()
            return dict(row) if row is not None else None
        finally:
            cursor.close()

    def _scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        row = self._fetchone(sql, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    # ------------------------------------------------------- writer plumbing

    def _run(
        self, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any],
    ) -> Any:
        self._writer_ident = threading.get_ident()
        try:
            return func(*args, **kwargs)
        finally:
            self._writer_ident = None

    async def _call(
        self, func: Callable[..., Any], /, *args: Any, **kwargs: Any,
    ) -> Any:
        if self._connection is None:
            raise RuntimeError("The database is not open")
        if self._writer_ident == threading.get_ident():
            return func(*args, **kwargs)
        executor = self._executor
        if executor is None:
            raise RuntimeError("The database is not open")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            executor, functools.partial(self._run, func, args, kwargs),
        )

    # -------------------------------------------------------- lifecycle

    async def open(self) -> None:
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None, timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._connection = connection
        self._migrate(connection)
        if os.name != "nt":
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        reader = await aiosqlite.connect(self.path, isolation_level=None, timeout=5.0)
        reader.row_factory = aiosqlite.Row
        await reader.execute("PRAGMA busy_timeout=5000")
        await reader.execute("PRAGMA query_only=ON")
        self._reader = reader
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="enan-db")
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        executor = self._executor
        self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)
        reader, self._reader = self._reader, None
        if reader is not None:
            await reader.close()
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _db(self) -> aiosqlite.Connection:
        """Return the shared read-only connection used for ad-hoc queries."""
        if self._reader is None:
            raise RuntimeError("The database is not open")
        return self._reader

    async def health_check(self) -> bool:
        try:
            return bool(await self._call(self._health_check_sync))
        except Exception:
            return False

    def _health_check_sync(self) -> bool:
        return bool(self._scalar("SELECT 1 AS ok", (), 0))

    # -------------------------------------------------------- migrations

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
        )
        applied = {
            int(row[0]) for row in connection.execute("SELECT version FROM schema_version")
        }
        for version, script in _MILESTONES:
            connection.executescript(script)
            if version not in applied:
                connection.execute(
                    "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?,?)",
                    (version, self._now()),
                )
        self._ensure_column(connection, "reminders", "repeat_seconds", "INTEGER")
        self._ensure_column(connection, "reminders", "lease_until", "REAL")
        self._ensure_column(connection, "reminders", "attempts", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(connection, "user_tasks", "due_at", "TEXT")
        self._ensure_column(connection, "admin_reply_map", "theme_id", "TEXT")
        self._ensure_column(connection, "admin_reply_map", "user_id", "INTEGER")
        self._ensure_column(connection, "sticker_registry", "file_unique_id", "TEXT")
        self._ensure_column(connection, "sticker_registry", "send_failures", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(connection, "evolution_proposals", "source_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(connection, "evolution_proposals", "candidate_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(connection, "audit_events", "previous_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column(connection, "audit_events", "event_hash", "TEXT NOT NULL DEFAULT ''")
        connection.execute(
            "INSERT OR IGNORE INTO update_upload_gate(id, state, updated_at) "
            "VALUES (1, 'disabled', ?)",
            (self._now(),),
        )
        self._rechain_audit(connection)

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, declaration: str,
    ) -> None:
        existing = {
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    # ------------------------------------------------------------ audit chain

    @staticmethod
    def _audit_digest(
        row_id: int, actor_id: Any, action: str, target: str, outcome: str,
        created_at: str, previous_hash: str,
    ) -> str:
        payload = "\x00".join(
            str(part) for part in (
                row_id, actor_id, action, target, outcome, created_at, previous_hash,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _rechain_audit(self, connection: sqlite3.Connection) -> None:
        previous = ""
        cursor = connection.execute(
            "SELECT id, actor_id, action, target, outcome, created_at, "
            "previous_hash, event_hash FROM audit_events ORDER BY id",
        )
        for row in cursor.fetchall():
            digest = self._audit_digest(
                int(row["id"]), row["actor_id"], str(row["action"]),
                str(row["target"]), str(row["outcome"]), str(row["created_at"]), previous,
            )
            if row["previous_hash"] != previous or row["event_hash"] != digest:
                connection.execute(
                    "UPDATE audit_events SET previous_hash=?, event_hash=? WHERE id=?",
                    (previous, digest, int(row["id"])),
                )
            previous = digest
        cursor.close()

    def _verify_audit_chain_sync(self) -> dict[str, Any]:
        previous = ""
        checked = 0
        for row in self._fetchall(
            "SELECT id, actor_id, action, target, outcome, created_at, "
            "previous_hash, event_hash FROM audit_events ORDER BY id",
        ):
            checked += 1
            if str(row["previous_hash"]) != previous:
                return {"valid": False, "reason": "chain_break", "checked": checked}
            expected = self._audit_digest(
                int(row["id"]), row["actor_id"], str(row["action"]),
                str(row["target"]), str(row["outcome"]), str(row["created_at"]), previous,
            )
            if str(row["event_hash"]) != expected:
                return {"valid": False, "reason": "modified", "checked": checked}
            previous = expected
        return {"valid": True, "reason": "ok", "checked": checked}

    async def verify_audit_chain(self) -> dict[str, Any]:
        return await self._call(self._verify_audit_chain_sync)

    def _audit_sync(
        self, actor_id: int, action: str, target: str = "", outcome: str = "",
    ) -> int:
        clean_action = str(action)[:200]
        clean_target = str(target)[:300]
        clean_outcome = str(outcome)[:300]
        created_at = self._now()
        with self._transaction() as connection:
            previous = str(self._scalar(
                "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1", (), "",
            ))
            cursor = connection.execute(
                "INSERT INTO audit_events(actor_id, action, target, outcome, created_at, "
                "previous_hash, event_hash) VALUES (?,?,?,?,?,?,'')",
                (int(actor_id), clean_action, clean_target, clean_outcome,
                 created_at, previous),
            )
            event_id = int(cursor.lastrowid or 0)
            digest = self._audit_digest(
                event_id, int(actor_id), clean_action, clean_target, clean_outcome,
                created_at, previous,
            )
            connection.execute(
                "UPDATE audit_events SET event_hash=? WHERE id=?", (digest, event_id),
            )
        return event_id

    async def audit(
        self, actor_id: int, action: str, target: str = "", outcome: str = "",
    ) -> int:
        return await self._call(self._audit_sync, actor_id, action, target, outcome)

    async def recent_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._call(self._recent_audit_events_sync, limit)

    def _recent_audit_events_sync(self, limit: int) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, actor_id, action, target, outcome, created_at "
            "FROM audit_events ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )

    # ------------------------------------------------------------ settings

    def _get_setting_sync(self, key: str, default: str | None = None) -> str | None:
        row = self._fetchone("SELECT value FROM settings WHERE key=?", (str(key),))
        return default if row is None else str(row["value"])

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        return await self._call(self._get_setting_sync, key, default)

    def _set_setting_sync(self, key: str, value: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (str(key), str(value), self._now()),
            )

    async def set_setting(self, key: str, value: str) -> None:
        await self._call(self._set_setting_sync, key, value)

    # ------------------------------------------------------- global access mode

    def _get_global_access_mode_sync(self) -> str:
        return str(self._get_setting_sync("global_access_mode", "private") or "private")

    async def get_global_access_mode(self) -> str:
        return await self._call(self._get_global_access_mode_sync)

    def _set_global_access_mode_sync(self, mode: str, actor_id: int) -> None:
        selected = str(mode).casefold()
        if selected not in {"private", "public"}:
            raise ValueError("Global access mode must be private or public")
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES ('global_access_mode',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (selected, self._now()),
            )
            # Written through the public method so the audit trail stays consistent.
            self._audit_sync(
                int(actor_id), "global_access_mode.change", selected,
                f"global access mode changed to {selected}",
            )

    async def set_global_access_mode(self, mode: str, actor_id: int) -> None:
        await self._call(self._set_global_access_mode_sync, mode, actor_id)

    # ---------------------------------------------------------- preferences

    def _get_preference_sync(self, user_id: int, key: str, default: str) -> str:
        row = self._fetchone(
            "SELECT value FROM user_preferences WHERE user_id=? AND key=?",
            (int(user_id), str(key)),
        )
        return default if row is None else str(row["value"])

    async def get_preference(self, user_id: int, key: str, default: str = "") -> str:
        return await self._call(self._get_preference_sync, user_id, key, default)

    def _set_preference_sync(self, user_id: int, key: str, value: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO user_preferences(user_id, key, value, updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (int(user_id), str(key)[:100], str(value)[:4000], self._now()),
            )

    async def set_preference(self, user_id: int, key: str, value: str) -> None:
        await self._call(self._set_preference_sync, user_id, key, value)

    def _delete_preferences_sync(
        self, user_id: int, keys: Iterable[str] | None, prefix: str | None,
    ) -> int:
        with self._transaction() as connection:
            removed = 0
            if keys:
                placeholders = ",".join("?" for _ in keys)
                cursor = connection.execute(
                    f"DELETE FROM user_preferences WHERE user_id=? AND key IN ({placeholders})",
                    (int(user_id), *[str(key) for key in keys]),
                )
                removed += int(cursor.rowcount or 0)
            if prefix:
                escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                cursor = connection.execute(
                    "DELETE FROM user_preferences WHERE user_id=? AND key LIKE ? ESCAPE '\\'",
                    (int(user_id), f"{escaped}%"),
                )
                removed += int(cursor.rowcount or 0)
        return removed

    async def delete_preferences(
        self, user_id: int, keys: Iterable[str] | None = None, prefix: str | None = None,
    ) -> int:
        return await self._call(self._delete_preferences_sync, user_id, keys, prefix)

    # ------------------------------------------------------------- messages

    def _save_message_sync(self, chat_id: int, role: str, content: str, keep: int) -> None:
        selected = str(role).casefold()
        if selected not in {"user", "assistant"}:
            raise ValueError("Message role must be user or assistant")
        limit = max(2, int(keep))
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?,?,?,?)",
                (int(chat_id), selected, str(content), self._now()),
            )
            connection.execute(
                "DELETE FROM messages WHERE chat_id=? AND id NOT IN ("
                "SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?)",
                (int(chat_id), int(chat_id), limit),
            )

    async def save_message(self, chat_id: int, role: str, content: str, keep: int = 12) -> None:
        await self._call(self._save_message_sync, chat_id, role, content, keep)

    def _history_sync(self, chat_id: int, limit: int) -> list[dict[str, Any]]:
        count = max(0, int(limit))
        if count == 0:
            return []
        rows = self._fetchall(
            "SELECT id, role, content, created_at FROM messages WHERE chat_id=? "
            "ORDER BY id DESC LIMIT ?",
            (int(chat_id), count),
        )
        rows.reverse()
        return rows

    async def history(self, chat_id: int, limit: int = 12) -> list[dict[str, Any]]:
        return await self._call(self._history_sync, chat_id, limit)

    def _search_history_sync(self, chat_id: int, query: str, limit: int) -> list[dict[str, Any]]:
        term = self._like(str(query))
        return self._fetchall(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE chat_id=? AND content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
            (int(chat_id), term, max(1, int(limit))),
        )

    async def search_history(
        self, chat_id: int, query: str, limit: int = 10,
    ) -> list[dict[str, Any]]:
        return await self._call(self._search_history_sync, chat_id, query, limit)

    def _clear_user_conversation_sync(self, user_id: int) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM messages WHERE chat_id=?", (int(user_id),))
            connection.execute("DELETE FROM memories WHERE user_id=?", (int(user_id),))
            connection.execute("DELETE FROM user_memories WHERE user_id=?", (int(user_id),))
            connection.execute("DELETE FROM learned_knowledge WHERE user_id=?", (int(user_id),))

    async def clear_user_conversation(self, user_id: int) -> None:
        await self._call(self._clear_user_conversation_sync, user_id)

    def _save_group_message_sync(
        self, chat_id: int, user_id: int, display_name: str, text: str,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO group_messages(chat_id, user_id, display_name, text, created_at) "
                "VALUES (?,?,?,?,?)",
                (int(chat_id), int(user_id), str(display_name)[:200], str(text)[:4000], self._now()),
            )
            connection.execute(
                "DELETE FROM group_messages WHERE chat_id=? AND id NOT IN ("
                "SELECT id FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT 100)",
                (int(chat_id), int(chat_id)),
            )

    async def save_group_message(
        self, chat_id: int, user_id: int, display_name: str, text: str,
    ) -> None:
        await self._call(self._save_group_message_sync, chat_id, user_id, display_name, text)

    def _recent_group_messages_sync(self, chat_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self._fetchall(
            "SELECT id, chat_id, user_id, display_name, text, created_at "
            "FROM group_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (int(chat_id), max(1, min(100, int(limit)))),
        )
        rows.reverse()
        return rows

    async def recent_group_messages(
        self, chat_id: int, limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._call(self._recent_group_messages_sync, chat_id, limit)

    # --------------------------------------------------------- chat settings

    def _get_chat_mode_sync(self, chat_id: int) -> str:
        row = self._fetchone("SELECT mode FROM chat_settings WHERE chat_id=?", (int(chat_id),))
        return "default" if row is None else str(row["mode"])

    async def get_chat_mode(self, chat_id: int) -> str:
        return await self._call(self._get_chat_mode_sync, chat_id)

    def _set_chat_mode_sync(self, chat_id: int, mode: str) -> None:
        selected = str(mode).casefold()
        if selected not in CHAT_MODES:
            raise ValueError("Unsupported chat mode")
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO chat_settings(chat_id, mode, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET mode=excluded.mode, "
                "updated_at=excluded.updated_at",
                (int(chat_id), selected, self._now()),
            )

    async def set_chat_mode(self, chat_id: int, mode: str) -> None:
        await self._call(self._set_chat_mode_sync, chat_id, mode)

    # ------------------------------------------------------- live sessions

    def _is_live_sync(self, user_id: int) -> bool:
        return bool(self._scalar(
            "SELECT active FROM live_sessions WHERE user_id=?", (int(user_id),), 0,
        ))

    async def is_live(self, user_id: int) -> bool:
        return await self._call(self._is_live_sync, user_id)

    def _set_live_sync(self, user_id: int, active: bool) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO live_sessions(user_id, active, started_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET active=excluded.active",
                (int(user_id), 1 if active else 0, self._now()),
            )

    async def set_live(self, user_id: int, active: bool) -> None:
        await self._call(self._set_live_sync, user_id, active)

    def _end_live_sync(self, user_id: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE live_sessions SET active=0 WHERE user_id=? AND active=1",
                (int(user_id),),
            )
            return int(cursor.rowcount or 0) > 0

    async def end_live(self, user_id: int) -> bool:
        return await self._call(self._end_live_sync, user_id)

    def _end_all_live_sync(self) -> int:
        with self._transaction() as connection:
            cursor = connection.execute("UPDATE live_sessions SET active=0 WHERE active=1")
            return int(cursor.rowcount or 0)

    async def end_all_live(self) -> int:
        return await self._call(self._end_all_live_sync)

    def _active_live_count_sync(self) -> int:
        return int(self._scalar("SELECT COUNT(*) FROM live_sessions WHERE active=1", (), 0))

    async def active_live_count(self) -> int:
        return await self._call(self._active_live_count_sync)

    # ------------------------------------------------------- admin reply map

    def _map_admin_message_sync(
        self, message_id: int, chat_id: int, user_id: int | None, theme_id: str | None,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO admin_reply_map(message_id, user_chat_id, user_id, theme_id, "
                "created_at) VALUES (?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET "
                "user_chat_id=excluded.user_chat_id, user_id=excluded.user_id, "
                "theme_id=excluded.theme_id",
                (int(message_id), int(chat_id),
                 None if user_id is None else int(user_id),
                 None if theme_id is None else str(theme_id), self._now()),
            )

    async def map_admin_message(
        self, message_id: int, chat_id: int,
        user_id: int | None = None, theme_id: str | None = None,
    ) -> None:
        await self._call(self._map_admin_message_sync, message_id, chat_id, user_id, theme_id)

    def _admin_reply_context_sync(self, message_id: int) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT message_id, user_chat_id, user_id, theme_id, created_at "
            "FROM admin_reply_map WHERE message_id=?",
            (int(message_id),),
        )

    async def admin_reply_context(self, message_id: int) -> dict[str, Any] | None:
        return await self._call(self._admin_reply_context_sync, message_id)

    # --------------------------------------------------------- moderation

    def _set_user_blocked_sync(self, user_id: int, blocked_by: int, reason: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO blocked_users(user_id, blocked_by, reason, blocked_at) "
                "VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "blocked_by=excluded.blocked_by, reason=excluded.reason, "
                "blocked_at=excluded.blocked_at",
                (int(user_id), int(blocked_by), str(reason)[:500], self._now()),
            )

    async def set_user_blocked(self, user_id: int, blocked_by: int, reason: str = "") -> None:
        await self._call(self._set_user_blocked_sync, user_id, blocked_by, reason)

    def _set_user_unblocked_sync(self, user_id: int, blocked_by: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM blocked_users WHERE user_id=?", (int(user_id),),
            )
            removed = int(cursor.rowcount or 0) > 0
            if removed:
                self._audit_sync(int(blocked_by), "moderation.unblock", str(user_id), "unblocked")
        return removed

    async def set_user_unblocked(self, user_id: int, blocked_by: int) -> bool:
        return await self._call(self._set_user_unblocked_sync, user_id, blocked_by)

    def _is_user_blocked_sync(self, user_id: int) -> bool:
        return self._fetchone(
            "SELECT user_id FROM blocked_users WHERE user_id=?", (int(user_id),),
        ) is not None

    async def is_user_blocked(self, user_id: int) -> bool:
        return await self._call(self._is_user_blocked_sync, user_id)

    # -------------------------------------------------------------- memories

    def _add_memory_sync(self, user_id: int, fact: str) -> int:
        text = " ".join(str(fact).split())
        if not text:
            raise ValueError("A memory fact cannot be empty")
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO memories(user_id, fact, created_at) VALUES (?,?,?)",
                (int(user_id), text[:2000], self._now()),
            )
            return int(cursor.lastrowid or 0)

    async def add_memory(self, user_id: int, fact: str) -> int:
        return await self._call(self._add_memory_sync, user_id, fact)

    def _memories_sync(self, user_id: int) -> list[str]:
        return [
            str(row["fact"]) for row in self._fetchall(
                "SELECT fact FROM memories WHERE user_id=? ORDER BY id", (int(user_id),),
            )
        ]

    async def memories(self, user_id: int) -> list[str]:
        return await self._call(self._memories_sync, user_id)

    def _forget_memory_sync(self, user_id: int, memory_id: int | None) -> int:
        with self._transaction() as connection:
            if memory_id is None:
                cursor = connection.execute(
                    "DELETE FROM memories WHERE user_id=?", (int(user_id),),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM memories WHERE user_id=? AND id=?",
                    (int(user_id), int(memory_id)),
                )
            return int(cursor.rowcount or 0)

    async def forget_memory(self, user_id: int, memory_id: int | None = None) -> int:
        return await self._call(self._forget_memory_sync, user_id, memory_id)

    def _save_user_fact_sync(self, user_id: int, key: str, value: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO user_memories(user_id, key, value, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (int(user_id), str(key)[:100], str(value)[:500], self._now()),
            )

    async def save_user_fact(self, user_id: int, key: str, value: str) -> None:
        await self._call(self._save_user_fact_sync, user_id, key, value)

    def _get_user_facts_sync(self, user_id: int) -> dict[str, str]:
        return {
            str(row["key"]): str(row["value"]) for row in self._fetchall(
                "SELECT key, value FROM user_memories WHERE user_id=? ORDER BY key",
                (int(user_id),),
            )
        }

    async def get_user_facts(self, user_id: int) -> dict[str, str]:
        return await self._call(self._get_user_facts_sync, user_id)

    def _save_learned_knowledge_sync(
        self, user_id: int, category: str, key: str, value: str,
        source_user_id: int | None,
    ) -> None:
        clean_key = " ".join(str(key).split())
        clean_value = " ".join(str(value).split())
        if not clean_key or not clean_value:
            raise ValueError("A learned fact needs a key and a value")
        if len(clean_key) > 200 or len(clean_value) > 1000:
            raise ValueError("A learned fact is too long")
        now = self._now()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO learned_knowledge(user_id, category, fact_key, fact_value, "
                "source_user_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id, category, fact_key) DO UPDATE SET "
                "fact_value=excluded.fact_value, updated_at=excluded.updated_at",
                (int(user_id), str(category)[:50], clean_key, clean_value,
                 None if source_user_id is None else int(source_user_id), now, now),
            )

    async def save_learned_knowledge(
        self, user_id: int, category: str, key: str, value: str,
        source_user_id: int | None = None,
    ) -> None:
        await self._call(
            self._save_learned_knowledge_sync, user_id, category, key, value, source_user_id,
        )

    def _get_learned_knowledge_sync(self, user_id: int) -> list[dict[str, str]]:
        return [
            {
                "category": str(row["category"]),
                "key": str(row["fact_key"]),
                "value": str(row["fact_value"]),
            }
            for row in self._fetchall(
                "SELECT category, fact_key, fact_value FROM learned_knowledge "
                "WHERE user_id=? ORDER BY updated_at DESC LIMIT 100",
                (int(user_id),),
            )
        ]

    async def get_learned_knowledge(self, user_id: int) -> list[dict[str, str]]:
        return await self._call(self._get_learned_knowledge_sync, user_id)

    # ------------------------------------------------------------- documents

    def _save_document_sync(
        self, chat_id: int, filename: str, mime_type: str,
        chunks: Sequence[tuple[str, str]],
    ) -> int:
        expires = (
            datetime.now(UTC) + timedelta(hours=self.document_retention_hours)
        ).isoformat()
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO documents(chat_id, filename, mime_type, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (int(chat_id), str(filename)[:255], str(mime_type)[:120], self._now(), expires),
            )
            document_id = int(cursor.lastrowid or 0)
            for index, (source, content) in enumerate(chunks):
                connection.execute(
                    "INSERT INTO document_chunks(document_id, source, content, chunk_index) "
                    "VALUES (?,?,?,?)",
                    (document_id, str(source)[:200], str(content), index),
                )
        return document_id

    async def save_document(
        self, chat_id: int, filename: str, mime_type: str,
        chunks: Sequence[tuple[str, str]],
    ) -> int:
        return await self._call(self._save_document_sync, chat_id, filename, mime_type, chunks)

    def _list_documents_sync(self, chat_id: int) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT d.id, d.filename, d.mime_type, d.created_at, d.expires_at, "
            "(SELECT COUNT(*) FROM document_chunks c WHERE c.document_id=d.id) AS chunks "
            "FROM documents d WHERE d.chat_id=? AND d.expires_at > ? ORDER BY d.id DESC",
            (int(chat_id), self._now()),
        )

    async def list_documents(self, chat_id: int) -> list[dict[str, Any]]:
        return await self._call(self._list_documents_sync, chat_id)

    def _search_documents_sync(
        self, chat_id: int, query: str, limit: int,
    ) -> list[dict[str, Any]]:
        term = self._like(str(query))
        return self._fetchall(
            "SELECT d.filename, c.source, c.content FROM document_chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE d.chat_id=? AND d.expires_at > ? "
            "AND c.content LIKE ? ESCAPE '\\' ORDER BY c.id DESC LIMIT ?",
            (int(chat_id), self._now(), term, max(1, int(limit))),
        )

    async def search_documents(
        self, chat_id: int, query: str, limit: int = 6,
    ) -> list[dict[str, Any]]:
        return await self._call(self._search_documents_sync, chat_id, query, limit)

    def _document_for_export_sync(
        self, chat_id: int, document_id: int,
    ) -> dict[str, Any] | None:
        document = self._fetchone(
            "SELECT id, filename, mime_type FROM documents WHERE id=? AND chat_id=?",
            (int(document_id), int(chat_id)),
        )
        if document is None:
            return None
        chunks = [
            {"source": str(row["source"]), "content": str(row["content"])}
            for row in self._fetchall(
                "SELECT source, content FROM document_chunks WHERE document_id=? "
                "ORDER BY chunk_index",
                (int(document_id),),
            )
        ]
        if not chunks:
            return None
        return {
            "id": int(document["id"]),
            "filename": str(document["filename"]),
            "mime_type": str(document["mime_type"]),
            "chunks": chunks,
        }

    async def document_for_export(
        self, chat_id: int, document_id: int,
    ) -> dict[str, Any] | None:
        return await self._call(self._document_for_export_sync, chat_id, document_id)

    def _forget_documents_sync(self, chat_id: int) -> int:
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM documents WHERE chat_id=?", (int(chat_id),))
            return int(cursor.rowcount or 0)

    async def forget_documents(self, chat_id: int) -> int:
        return await self._call(self._forget_documents_sync, chat_id)

    def _cleanup_documents_sync(self) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM documents WHERE expires_at <= ?", (self._now(),),
            )
            return int(cursor.rowcount or 0)

    async def cleanup_documents(self) -> int:
        return await self._call(self._cleanup_documents_sync)

    # ---------------------------------------------------------- personal KB

    def _kb_usage_sync(self, user_id: int) -> tuple[int, int]:
        row = self._fetchone(
            "SELECT COUNT(*) AS documents, COALESCE(SUM(byte_size),0) AS total "
            "FROM kb_documents WHERE user_id=?",
            (int(user_id),),
        )
        if row is None:
            return 0, 0
        return int(row["documents"]), int(row["total"])

    def _promote_document_to_kb_sync(
        self, user_id: int, chat_id: int, document_id: int,
    ) -> int:
        source = self._fetchone(
            "SELECT id, filename, mime_type FROM documents WHERE id=? AND chat_id=?",
            (int(document_id), int(chat_id)),
        )
        if source is None:
            raise ValueError("That file index is missing, expired, or belongs to another chat.")
        chunks = self._fetchall(
            "SELECT source, content FROM document_chunks WHERE document_id=? ORDER BY chunk_index",
            (int(document_id),),
        )
        if not chunks:
            raise ValueError("This document has no extracted text to save.")
        payload = sum(len(str(row["content"]).encode("utf-8")) for row in chunks)
        documents, used = self._kb_usage_sync(int(user_id))
        if documents + 1 > self.kb_max_documents:
            raise RuntimeError("Your personal Knowledge Base document limit has been reached.")
        if used + payload > self.kb_max_bytes:
            raise RuntimeError("Your personal Knowledge Base storage limit has been reached.")
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO kb_documents(user_id, filename, mime_type, byte_size, created_at) "
                "VALUES (?,?,?,?,?)",
                (int(user_id), self._wrap(str(source["filename"])),
                 self._wrap(str(source["mime_type"])), payload, self._now()),
            )
            kb_id = int(cursor.lastrowid or 0)
            for index, row in enumerate(chunks):
                connection.execute(
                    "INSERT INTO kb_chunks(document_id, source, content, chunk_index) "
                    "VALUES (?,?,?,?)",
                    (kb_id, self._wrap(str(row["source"])),
                     self._wrap(str(row["content"])), index),
                )
        return kb_id

    async def promote_document_to_kb(self, user_id: int, chat_id: int, document_id: int) -> int:
        return await self._call(
            self._promote_document_to_kb_sync, user_id, chat_id, document_id,
        )

    def _list_kb_documents_sync(self, user_id: int) -> list[dict[str, Any]]:
        return [
            {
                "id": int(row["id"]),
                "filename": self._unwrap(row["filename"]),
                "mime_type": self._unwrap(row["mime_type"]),
                "byte_size": int(row["byte_size"]),
                "created_at": str(row["created_at"]),
            }
            for row in self._fetchall(
                "SELECT id, filename, mime_type, byte_size, created_at "
                "FROM kb_documents WHERE user_id=? ORDER BY id DESC",
                (int(user_id),),
            )
        ]

    async def list_kb_documents(self, user_id: int) -> list[dict[str, Any]]:
        return await self._call(self._list_kb_documents_sync, user_id)

    def _search_kb_sync(self, user_id: int, query: str, limit: int) -> list[dict[str, Any]]:
        term = str(query).casefold()
        results: list[dict[str, Any]] = []
        for row in self._fetchall(
            "SELECT d.filename, c.source, c.content FROM kb_chunks c "
            "JOIN kb_documents d ON d.id=c.document_id WHERE d.user_id=? ORDER BY c.id DESC",
            (int(user_id),),
        ):
            content = self._unwrap(row["content"])
            if term and term not in content.casefold():
                continue
            results.append({
                "filename": self._unwrap(row["filename"]),
                "source": self._unwrap(row["source"]),
                "content": content,
            })
            if len(results) >= max(1, int(limit)):
                break
        return results

    async def search_kb(
        self, user_id: int, query: str, limit: int = 4,
    ) -> list[dict[str, Any]]:
        return await self._call(self._search_kb_sync, user_id, query, limit)

    def _delete_kb_document_sync(self, user_id: int, document_id: int | None) -> int:
        with self._transaction() as connection:
            if document_id is None:
                cursor = connection.execute(
                    "DELETE FROM kb_documents WHERE user_id=?", (int(user_id),),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM kb_documents WHERE user_id=? AND id=?",
                    (int(user_id), int(document_id)),
                )
            return int(cursor.rowcount or 0)

    async def delete_kb_document(self, user_id: int, document_id: int | None = None) -> int:
        return await self._call(self._delete_kb_document_sync, user_id, document_id)

    # -------------------------------------------------------------- tasks

    def _add_task_sync(
        self, user_id: int, text: str, priority: str, due_at: str | None,
        depends_on: Sequence[int],
    ) -> int:
        description = " ".join(str(text).split())
        if not description or len(description) > 500:
            raise ValueError("A task needs 1-500 characters")
        level = str(priority or "normal").casefold()
        if level not in TASK_PRIORITIES:
            raise ValueError("Unsupported task priority")
        prerequisites = [int(item) for item in (depends_on or ())]
        now = self._now()
        with self._transaction() as connection:
            open_count = int(self._scalar(
                "SELECT COUNT(*) FROM user_tasks WHERE user_id=? AND status='open'",
                (int(user_id),), 0,
            ))
            if open_count >= 100:
                raise ValueError("open task limit reached")
            if prerequisites:
                placeholders = ",".join("?" for _ in prerequisites)
                owned = int(self._scalar(
                    f"SELECT COUNT(*) FROM user_tasks WHERE user_id=? AND id IN ({placeholders})",
                    (int(user_id), *prerequisites), 0,
                ))
                if owned != len(set(prerequisites)):
                    raise ValueError("Every prerequisite must belong to this task list")
            cursor = connection.execute(
                "INSERT INTO user_tasks(user_id, task_text, status, priority, due_at, "
                "created_at, updated_at) VALUES (?,?,'open',?,?,?,?)",
                (int(user_id), description, level, None if due_at is None else str(due_at),
                 now, now),
            )
            task_id = int(cursor.lastrowid or 0)
            for prerequisite in dict.fromkeys(prerequisites):
                if prerequisite == task_id:
                    raise ValueError("A task cannot depend on itself")
                connection.execute(
                    "INSERT OR IGNORE INTO task_dependencies(task_id, depends_on_task_id) "
                    "VALUES (?,?)",
                    (task_id, prerequisite),
                )
        return task_id

    async def add_task(
        self, user_id: int, text: str, priority: str = "normal",
        due_at: str | None = None, depends_on: Sequence[int] = (),
    ) -> int:
        return await self._call(self._add_task_sync, user_id, text, priority, due_at, depends_on)

    def _task_rows_sync(self, user_id: int) -> list[dict[str, Any]]:
        tasks = self._fetchall(
            "SELECT id, task_text, status, priority, due_at, created_at, completed_at "
            "FROM user_tasks WHERE user_id=? ORDER BY status='done', id",
            (int(user_id),),
        )
        links = self._fetchall(
            "SELECT d.task_id, d.depends_on_task_id, p.status AS prerequisite_status "
            "FROM task_dependencies d JOIN user_tasks p ON p.id=d.depends_on_task_id "
            "JOIN user_tasks t ON t.id=d.task_id WHERE t.user_id=? ORDER BY d.depends_on_task_id",
            (int(user_id),),
        )
        dependencies: dict[int, list[int]] = {}
        blocked: dict[int, list[int]] = {}
        for link in links:
            task_id = int(link["task_id"])
            prerequisite = int(link["depends_on_task_id"])
            dependencies.setdefault(task_id, []).append(prerequisite)
            if str(link["prerequisite_status"]) != "done":
                blocked.setdefault(task_id, []).append(prerequisite)
        for task in tasks:
            task_id = int(task["id"])
            task["depends_on"] = dependencies.get(task_id, [])
            task["blocked_by"] = blocked.get(task_id, [])
            task["blocked_count"] = len(task["blocked_by"])
        return tasks

    async def list_tasks(self, user_id: int) -> list[dict[str, Any]]:
        return await self._call(self._task_rows_sync, user_id)

    def _complete_task_sync(self, user_id: int, task_id: int) -> bool:
        with self._transaction() as connection:
            blocked = int(self._scalar(
                "SELECT COUNT(*) FROM task_dependencies d "
                "JOIN user_tasks p ON p.id=d.depends_on_task_id "
                "WHERE d.task_id=? AND p.status<>'done'",
                (int(task_id),), 0,
            ))
            if blocked:
                return False
            cursor = connection.execute(
                "UPDATE user_tasks SET status='done', completed_at=?, updated_at=? "
                "WHERE id=? AND user_id=? AND status='open'",
                (self._now(), self._now(), int(task_id), int(user_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def complete_task(self, user_id: int, task_id: int) -> bool:
        return await self._call(self._complete_task_sync, user_id, task_id)

    def _delete_task_sync(self, user_id: int, task_id: int) -> bool:
        with self._transaction() as connection:
            dependents = int(self._scalar(
                "SELECT COUNT(*) FROM task_dependencies d JOIN user_tasks t ON t.id=d.task_id "
                "WHERE d.depends_on_task_id=? AND t.status='open'",
                (int(task_id),), 0,
            ))
            if dependents:
                raise ValueError("This task has open dependent tasks")
            cursor = connection.execute(
                "DELETE FROM user_tasks WHERE id=? AND user_id=?", (int(task_id), int(user_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def delete_task(self, user_id: int, task_id: int) -> bool:
        return await self._call(self._delete_task_sync, user_id, task_id)

    def _add_task_plan_sync(
        self, user_id: int, steps: Sequence[dict[str, Any]],
    ) -> list[int]:
        if not isinstance(steps, Sequence) or not steps:
            raise ValueError("A task plan needs at least one step")
        created: list[int] = []
        now = self._now()
        with self._transaction() as connection:
            for index, step in enumerate(steps[:20]):
                if not isinstance(step, dict):
                    raise ValueError("Task plan steps must be objects")
                description = " ".join(str(step.get("text", "")).split())
                if not description:
                    continue
                level = str(step.get("priority") or "normal").casefold()
                if level not in TASK_PRIORITIES:
                    level = "normal"
                due_at = step.get("due_at")
                cursor = connection.execute(
                    "INSERT INTO user_tasks(user_id, task_text, status, priority, due_at, "
                    "created_at, updated_at) VALUES (?,?,'open',?,?,?,?)",
                    (int(user_id), description[:500], level,
                     None if due_at in (None, "") else str(due_at)[:32], now, now),
                )
                task_id = int(cursor.lastrowid or 0)
                created.append(task_id)
                for reference in step.get("depends_on") or ():
                    try:
                        position = int(reference)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= position <= index and created:
                        connection.execute(
                            "INSERT OR IGNORE INTO task_dependencies("
                            "task_id, depends_on_task_id) VALUES (?,?)",
                            (task_id, created[position - 1]),
                        )
        return created

    async def add_task_plan(
        self, user_id: int, steps: Sequence[dict[str, Any]],
    ) -> list[int]:
        return await self._call(self._add_task_plan_sync, user_id, steps)

    # ---------------------------------------------------------- study cards

    def _add_study_card_sync(self, user_id: int, question: str, answer: str) -> int:
        prompt = " ".join(str(question).split())
        reply = " ".join(str(answer).split())
        if not prompt or not reply:
            raise ValueError("A study card needs a question and an answer")
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO study_cards(user_id, question, answer, due_at, interval_days, "
                "repetitions, created_at) VALUES (?,?,?,?,1,0,?)",
                (int(user_id), prompt[:1000], reply[:4000], self._now(), self._now()),
            )
            return int(cursor.lastrowid or 0)

    async def add_study_card(self, user_id: int, question: str, answer: str) -> int:
        return await self._call(self._add_study_card_sync, user_id, question, answer)

    def _list_study_cards_sync(self, user_id: int, due_only: bool) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, question, answer, due_at, interval_days, repetitions "
            "FROM study_cards WHERE user_id=?"
        )
        params: list[Any] = [int(user_id)]
        if due_only:
            sql += " AND due_at <= ?"
            params.append(self._now())
        sql += " ORDER BY due_at, id LIMIT 50"
        return self._fetchall(sql, params)

    async def list_study_cards(self, user_id: int, due_only: bool = False) -> list[dict[str, Any]]:
        return await self._call(self._list_study_cards_sync, user_id, due_only)

    def _review_study_card_sync(self, user_id: int, card_id: int, remembered: bool) -> bool:
        row = self._fetchone(
            "SELECT interval_days, repetitions FROM study_cards WHERE id=? AND user_id=?",
            (int(card_id), int(user_id)),
        )
        if row is None:
            return False
        previous = max(1, int(row["interval_days"] or 1))
        repetitions = int(row["repetitions"] or 0)
        interval = min(365, previous * 2) if remembered else 1
        next_due = (datetime.now(UTC) + timedelta(days=interval)).isoformat()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE study_cards SET due_at=?, interval_days=?, repetitions=?, "
                "reviewed_at=? WHERE id=? AND user_id=?",
                (next_due, interval, repetitions + 1 if remembered else 0,
                 self._now(), int(card_id), int(user_id)),
            )
        return True

    async def review_study_card(self, user_id: int, card_id: int, remembered: bool) -> bool:
        return await self._call(self._review_study_card_sync, user_id, card_id, remembered)

    # ------------------------------------------------------------ reminders

    def _add_reminder_sync(
        self, user_id: int, chat_id: int, text: str, run_at: datetime, repeat_seconds: int | None,
    ) -> int:
        moment = run_at if run_at.tzinfo else run_at.replace(tzinfo=UTC)
        pending = int(self._scalar(
            "SELECT COUNT(*) FROM reminders WHERE user_id=? AND delivered=0", (int(user_id),), 0,
        ))
        if pending >= self.max_pending_reminders:
            raise ValueError("pending reminder limit reached")
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO reminders(user_id, chat_id, reminder_text, run_at, run_at_epoch, "
                "repeat_seconds, created_at) VALUES (?,?,?,?,?,?,?)",
                (int(user_id), int(chat_id), " ".join(str(text).split())[:500], moment.isoformat(),
                 moment.timestamp(), None if repeat_seconds is None else int(repeat_seconds),
                 self._now()),
            )
            return int(cursor.lastrowid or 0)

    async def add_reminder(
        self, user_id: int, chat_id: int, text: str, run_at: datetime,
        repeat_seconds: int | None = None,
    ) -> int:
        return await self._call(
            self._add_reminder_sync, user_id, chat_id, text, run_at, repeat_seconds,
        )

    def _list_reminders_sync(self, user_id: int) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, chat_id, reminder_text, run_at, repeat_seconds, paused "
            "FROM reminders WHERE user_id=? AND delivered=0 ORDER BY run_at_epoch, id",
            (int(user_id),),
        )

    async def list_reminders(self, user_id: int) -> list[dict[str, Any]]:
        return await self._call(self._list_reminders_sync, user_id)

    def _claim_due_reminders_sync(self, now: float, lease_seconds: float) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = [
                dict(row) for row in connection.execute(
                    "SELECT id, chat_id, reminder_text, attempts, repeat_seconds FROM reminders "
                    "WHERE delivered=0 AND paused=0 AND run_at_epoch<=? "
                    "AND (lease_until IS NULL OR lease_until<=?) ORDER BY run_at_epoch, id LIMIT 50",
                    (now, now),
                ).fetchall()
            ]
            for row in rows:
                connection.execute(
                    "UPDATE reminders SET lease_until=?, attempts=attempts+1 WHERE id=?",
                    (now + lease_seconds, int(row["id"])),
                )
                row["attempts"] = int(row["attempts"]) + 1
        return rows

    async def claim_due_reminders(self) -> list[dict[str, Any]]:
        return await self._call(self._claim_due_reminders_sync, time.time(), 60.0)

    def _defer_reminder_sync(self, reminder_id: int, delay_seconds: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET lease_until=? WHERE id=? AND delivered=0",
                (time.time() + max(1, int(delay_seconds)), int(reminder_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def defer_reminder(self, reminder_id: int, delay_seconds: int) -> bool:
        return await self._call(self._defer_reminder_sync, reminder_id, delay_seconds)

    def _mark_reminder_delivered_sync(self, reminder_id: int) -> bool:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT repeat_seconds FROM reminders WHERE id=?", (int(reminder_id),),
            ).fetchone()
            if row is None:
                return False
            repeat = row["repeat_seconds"]
            if repeat:
                moment = (datetime.now(UTC) + timedelta(seconds=int(repeat)))
                connection.execute(
                    "UPDATE reminders SET run_at=?, run_at_epoch=?, delivered=0, attempts=0, "
                    "lease_until=NULL WHERE id=?",
                    (moment.isoformat(), moment.timestamp(), int(reminder_id)),
                )
            else:
                connection.execute(
                    "UPDATE reminders SET delivered=1, lease_until=NULL WHERE id=?",
                    (int(reminder_id),),
                )
        return True

    async def mark_reminder_delivered(self, reminder_id: int) -> bool:
        return await self._call(self._mark_reminder_delivered_sync, reminder_id)

    def _cancel_reminder_sync(self, user_id: int, reminder_id: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM reminders WHERE id=? AND user_id=? AND delivered=0",
                (int(reminder_id), int(user_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def cancel_reminder(self, user_id: int, reminder_id: int) -> bool:
        return await self._call(self._cancel_reminder_sync, user_id, reminder_id)

    def _set_reminder_paused_sync(self, user_id: int, reminder_id: int, paused: bool) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET paused=?, lease_until=NULL "
                "WHERE id=? AND user_id=? AND delivered=0",
                (1 if paused else 0, int(reminder_id), int(user_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def set_reminder_paused(self, user_id: int, reminder_id: int, paused: bool) -> bool:
        return await self._call(self._set_reminder_paused_sync, user_id, reminder_id, paused)

    def _snooze_reminder_sync(self, user_id: int, reminder_id: int, delay_seconds: int) -> bool:
        moment = datetime.now(UTC) + timedelta(seconds=max(1, int(delay_seconds)))
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE reminders SET run_at=?, run_at_epoch=?, delivered=0, paused=0, "
                "lease_until=NULL WHERE id=? AND user_id=? AND delivered=0",
                (moment.isoformat(), moment.timestamp(), int(reminder_id), int(user_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def snooze_reminder(self, user_id: int, reminder_id: int, delay_seconds: int) -> bool:
        return await self._call(self._snooze_reminder_sync, user_id, reminder_id, delay_seconds)

    # ---------------------------------------------------------------- usage

    def _reserve_usage_sync(
        self, user_id: int, model: str, estimated_cost: float, daily_limit: int,
        daily_cost_limit: float, monthly_limit: int, monthly_cost_limit: float,
    ) -> tuple[int | None, str]:
        now = datetime.now(UTC)
        day_start = self._day_start(now)
        month_start = self._month_start(now)
        estimate = max(0.0, float(estimated_cost or 0))
        with self._transaction() as connection:
            daily = int(self._scalar(
                "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), day_start), 0,
            ))
            if daily >= int(daily_limit):
                return None, "request_limit"
            monthly = int(self._scalar(
                "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), month_start), 0,
            ))
            if monthly >= int(monthly_limit):
                return None, "monthly_request_limit"
            monthly_cost = float(self._scalar(
                "SELECT COALESCE(SUM(COALESCE(actual_cost,estimated_cost)),0) "
                "FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), month_start), 0.0,
            ))
            if monthly_cost + estimate > float(monthly_cost_limit):
                return None, "monthly_cost_limit"
            bot_cost = float(self._scalar(
                "SELECT COALESCE(SUM(COALESCE(actual_cost,estimated_cost)),0) "
                "FROM usage_events WHERE created_at>=?", (day_start,), 0.0,
            ))
            if bot_cost + estimate > float(daily_cost_limit):
                return None, "daily_cost_limit"
            cursor = connection.execute(
                "INSERT INTO usage_events(user_id, model, estimated_cost, actual_cost, "
                "input_tokens, output_tokens, status, created_at) "
                "VALUES (?,?,?,NULL,0,0,'reserved',?)",
                (int(user_id), str(model)[:200], estimate, now.isoformat()),
            )
            return int(cursor.lastrowid or 0), ""

    async def reserve_usage(
        self, user_id: int, model: str, estimated_cost: float, daily_limit: int,
        daily_cost_limit: float, monthly_limit: int, monthly_cost_limit: float,
    ) -> tuple[int | None, str]:
        return await self._call(
            self._reserve_usage_sync, user_id, model, estimated_cost, daily_limit,
            daily_cost_limit, monthly_limit, monthly_cost_limit,
        )

    def _finish_usage_sync(
        self, event_id: int, model: str, usage: dict[str, Any] | None,
    ) -> None:
        values = usage or {}
        input_tokens = int(values.get("input_tokens") or values.get("prompt_tokens") or 0)
        output_tokens = int(
            values.get("output_tokens") or values.get("completion_tokens") or 0
        )
        with self._transaction() as connection:
            connection.execute(
                "UPDATE usage_events SET model=?, status='complete', input_tokens=?, "
                "output_tokens=?, actual_cost=COALESCE(actual_cost, estimated_cost) "
                "WHERE id=?",
                (str(model)[:200], input_tokens, output_tokens, int(event_id)),
            )

    async def finish_usage(
        self, event_id: int, model: str, usage: dict[str, Any] | None = None,
    ) -> None:
        await self._call(self._finish_usage_sync, event_id, model, usage)

    def _usage_today_sync(self, user_id: int) -> dict[str, Any]:
        now = datetime.now(UTC)
        day_start = self._day_start(now)
        month_start = self._month_start(now)
        return {
            "requests": int(self._scalar(
                "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), day_start), 0,
            )),
            "cost": float(self._scalar(
                "SELECT COALESCE(SUM(COALESCE(actual_cost,estimated_cost)),0) "
                "FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), day_start), 0.0,
            )),
            "monthly_requests": int(self._scalar(
                "SELECT COUNT(*) FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), month_start), 0,
            )),
            "monthly_cost": float(self._scalar(
                "SELECT COALESCE(SUM(COALESCE(actual_cost,estimated_cost)),0) "
                "FROM usage_events WHERE user_id=? AND created_at>=?",
                (int(user_id), month_start), 0.0,
            )),
        }

    async def usage_today(self, user_id: int) -> dict[str, Any]:
        return await self._call(self._usage_today_sync, user_id)

    def _dashboard_metrics_sync(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        day_start = self._day_start(now)
        return {
            "requests_today": int(self._scalar(
                "SELECT COUNT(*) FROM usage_events WHERE created_at>=?", (day_start,), 0,
            )),
            "cost_today_usd": float(self._scalar(
                "SELECT COALESCE(SUM(COALESCE(actual_cost,estimated_cost)),0) "
                "FROM usage_events WHERE created_at>=?", (day_start,), 0.0,
            )),
            "pending_approvals": int(self._scalar(
                "SELECT COUNT(*) FROM approvals WHERE status='pending'", (), 0,
            )),
            "running_agents": int(self._scalar(
                "SELECT COUNT(*) FROM agent_jobs WHERE status='running'", (), 0,
            )),
            "feedback_positive_today": int(self._scalar(
                "SELECT COUNT(*) FROM feedback WHERE rating>0 AND created_at>=?",
                (day_start,), 0,
            )),
            "feedback_negative_today": int(self._scalar(
                "SELECT COUNT(*) FROM feedback WHERE rating<0 AND created_at>=?",
                (day_start,), 0,
            )),
        }

    async def dashboard_metrics(self) -> dict[str, Any]:
        return await self._call(self._dashboard_metrics_sync)

    def _active_users_sync(self, limit: int) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT chat_id AS user_id, COUNT(*) AS message_count, "
            "MAX(created_at) AS last_active FROM messages WHERE chat_id>0 "
            "GROUP BY chat_id ORDER BY last_active DESC LIMIT ?",
            (max(1, int(limit)),),
        )

    async def active_users(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._call(self._active_users_sync, limit)

    # ----------------------------------------------------------- agent jobs

    def _create_agent_job_sync(self, user_id: int, task_type: str) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO agent_jobs(user_id, task_type, status, created_at) "
                "VALUES (?,?,'running',?)",
                (int(user_id), str(task_type)[:200], self._now()),
            )
            return int(cursor.lastrowid or 0)

    async def create_agent_job(self, user_id: int, task_type: str) -> int:
        return await self._call(self._create_agent_job_sync, user_id, task_type)

    def _finish_agent_job_sync(
        self, job_id: int, status: str, models: str, step_count: int,
        cost_usd: float, summary: str,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE agent_jobs SET status=?, models=?, step_count=?, cost_usd=?, "
                "summary=?, finished_at=? WHERE id=?",
                (str(status)[:32], str(models)[:400], int(step_count), float(cost_usd or 0),
                 str(summary)[:500], self._now(), int(job_id)),
            )

    async def finish_agent_job(
        self, job_id: int, status: str, models: str = "", step_count: int = 0,
        cost_usd: float = 0.0, summary: str = "",
    ) -> None:
        await self._call(
            self._finish_agent_job_sync, job_id, status, models, step_count, cost_usd, summary,
        )

    def _recent_agent_jobs_sync(self, limit: int) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, task_type, status, step_count, cost_usd, created_at "
            "FROM agent_jobs ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        )

    async def recent_agent_jobs(self, limit: int = 60) -> list[dict[str, Any]]:
        return await self._call(self._recent_agent_jobs_sync, limit)

    # ------------------------------------------------------------- feedback

    @staticmethod
    def _normalize_rating(rating: Any) -> int:
        if isinstance(rating, str):
            folded = rating.strip().casefold()
            if folded in {"up", "good", "positive", "1"}:
                return 1
            if folded in {"down", "bad", "negative", "-1"}:
                return -1
            return 0
        try:
            return max(-1, min(1, int(rating)))
        except (TypeError, ValueError):
            return 0

    def _record_feedback_sync(self, user_id: int, rating: Any, category: str) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO feedback(user_id, rating, category, created_at) VALUES (?,?,?,?)",
                (int(user_id), self._normalize_rating(rating), str(category)[:60], self._now()),
            )
            return int(cursor.lastrowid or 0)

    async def record_feedback(self, user_id: int, rating: Any, category: str) -> int:
        return await self._call(self._record_feedback_sync, user_id, rating, category)

    def _feedback_suggestions_sync(self, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "category": str(row["category"]),
                "negative_count": int(row["total"]),
                "suggestion": f"Review {row['category']} answers with an administrator.",
            }
            for row in self._fetchall(
                "SELECT category, COUNT(*) AS total FROM feedback WHERE rating<0 "
                "GROUP BY category HAVING total>=2 ORDER BY total DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        ]

    async def feedback_suggestions(self, limit: int = 5) -> list[dict[str, Any]]:
        return await self._call(self._feedback_suggestions_sync, limit)

    # ------------------------------------------------------------ approvals

    def _create_approval_sync(
        self, requester_id: int, kind: str, summary: str, payload: dict[str, Any],
        required_approver: str, ttl_seconds: int,
    ) -> int:
        approver = str(required_approver).casefold()
        if approver not in {"admin", "requester"}:
            raise ValueError("Approvals must target an admin or the requester")
        expires = (datetime.now(UTC) + timedelta(seconds=max(30, int(ttl_seconds)))).isoformat()
        serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO approvals(requester_id, kind, summary, payload, required_approver, "
                "status, created_at, expires_at) VALUES (?,?,?,?,?,'pending',?,?)",
                (int(requester_id), str(kind)[:60], str(summary)[:500], self._wrap(serialized),
                 approver, self._now(), expires),
            )
            return int(cursor.lastrowid or 0)

    async def create_approval(
        self, requester_id: int, kind: str, summary: str, payload: dict[str, Any],
        required_approver: str = "admin", ttl_seconds: int = 600,
    ) -> int:
        return await self._call(
            self._create_approval_sync, requester_id, kind, summary, payload,
            required_approver, ttl_seconds,
        )

    def _approval_row_sync(self, approval_id: int) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT id, requester_id, kind, summary, payload, required_approver, status, "
            "expires_at FROM approvals WHERE id=?",
            (int(approval_id),),
        )
        if row is None:
            return None
        try:
            row["payload"] = json.loads(self._unwrap(row["payload"]) or "{}")
        except json.JSONDecodeError:
            row["payload"] = {}
        return row

    @staticmethod
    def _approval_is_authorized(
        row: dict[str, Any], approver_id: int, admin_ids: Iterable[int],
    ) -> bool:
        if str(row["required_approver"]) == "admin":
            return int(approver_id) in {int(item) for item in admin_ids}
        return int(row["requester_id"]) == int(approver_id)

    def _consume_approval_sync(
        self, approval_id: int, approver_id: int, admin_ids: Iterable[int],
    ) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = self._approval_row_sync(approval_id)
            if row is None or str(row["status"]) != "pending":
                return None
            if str(row["expires_at"]) <= self._now():
                connection.execute(
                    "UPDATE approvals SET status='expired' WHERE id=?", (int(approval_id),),
                )
                return None
            if not self._approval_is_authorized(row, approver_id, admin_ids):
                return None
            connection.execute(
                "UPDATE approvals SET status='approved', resolved_at=?, resolved_by=? "
                "WHERE id=? AND status='pending'",
                (self._now(), int(approver_id), int(approval_id)),
            )
        return row

    async def consume_approval(
        self, approval_id: int, approver_id: int, admin_ids: Iterable[int],
    ) -> dict[str, Any] | None:
        return await self._call(
            self._consume_approval_sync, approval_id, approver_id, tuple(admin_ids),
        )

    def _finish_approval_sync(self, approval_id: int, state: str, note: str) -> bool:
        selected = str(state).casefold()
        if selected not in APPROVAL_STATUSES:
            raise ValueError("Unsupported approval state")
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status=?, note=?, resolved_at=? WHERE id=?",
                (selected, str(note)[:500], self._now(), int(approval_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def finish_approval(self, approval_id: int, state: str, note: str = "") -> bool:
        return await self._call(self._finish_approval_sync, approval_id, state, note)

    def _deny_approval_sync(
        self, approval_id: int, approver_id: int, admin_ids: Iterable[int],
    ) -> bool:
        with self._transaction() as connection:
            row = self._approval_row_sync(approval_id)
            if row is None or str(row["status"]) != "pending":
                return False
            if not self._approval_is_authorized(row, approver_id, admin_ids):
                return False
            cursor = connection.execute(
                "UPDATE approvals SET status='denied', resolved_at=?, resolved_by=? "
                "WHERE id=? AND status='pending'",
                (self._now(), int(approver_id), int(approval_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def deny_approval(
        self, approval_id: int, approver_id: int, admin_ids: Iterable[int],
    ) -> bool:
        return await self._call(
            self._deny_approval_sync, approval_id, approver_id, tuple(admin_ids),
        )

    def _pending_approvals_sync(self, limit: int) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT id, kind, summary, required_approver, created_at, expires_at "
            "FROM approvals WHERE status='pending' ORDER BY id LIMIT ?",
            (max(1, int(limit)),),
        )

    async def pending_approvals(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._call(self._pending_approvals_sync, limit)

    def _expire_approvals_sync(self) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status='expired' WHERE status='pending' AND expires_at<=?",
                (self._now(),),
            )
            return int(cursor.rowcount or 0)

    async def expire_approvals(self) -> int:
        return await self._call(self._expire_approvals_sync)

    # ------------------------------------------------------ action execution

    def _claim_action_execution_sync(
        self, chat_id: int, message_id: int, action_key: str, action_name: str, user_id: int,
    ) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO action_executions(chat_id, message_id, action_key, "
                "action_name, user_id, status, created_at) VALUES (?,?,?,?,?,'running',?)",
                (int(chat_id), int(message_id), str(action_key)[:128], str(action_name)[:60],
                 int(user_id), self._now()),
            )
            return int(cursor.rowcount or 0) > 0

    async def claim_action_execution(
        self, chat_id: int, message_id: int, action_key: str, action_name: str, user_id: int,
    ) -> bool:
        return await self._call(
            self._claim_action_execution_sync, chat_id, message_id, action_key,
            action_name, user_id,
        )

    def _finish_action_execution_sync(
        self, chat_id: int, message_id: int, action_key: str, status: str, result: str,
    ) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE action_executions SET status=?, result=?, finished_at=? "
                "WHERE chat_id=? AND message_id=? AND action_key=?",
                (str(status)[:20], str(result)[:500], self._now(),
                 int(chat_id), int(message_id), str(action_key)[:128]),
            )
            return int(cursor.rowcount or 0) > 0

    async def finish_action_execution(
        self, chat_id: int, message_id: int, action_key: str, status: str, result: str,
    ) -> bool:
        return await self._call(
            self._finish_action_execution_sync, chat_id, message_id, action_key, status, result,
        )

    def _cleanup_action_executions_sync(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM action_executions WHERE created_at<=?", (cutoff,),
            )
            return int(cursor.rowcount or 0)

    async def cleanup_action_executions(self) -> int:
        return await self._call(self._cleanup_action_executions_sync)

    # ------------------------------------------------------ update handling

    def _claim_update_sync(self, update_id: int) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO processed_updates(update_id, processed_at) VALUES (?,?)",
                (int(update_id), self._now()),
            )
            return int(cursor.rowcount or 0) > 0

    async def claim_update(self, update_id: int) -> bool:
        return await self._call(self._claim_update_sync, update_id)

    def _cleanup_processed_updates_sync(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM processed_updates WHERE processed_at<=?", (cutoff,),
            )
            return int(cursor.rowcount or 0)

    async def cleanup_processed_updates(self) -> int:
        return await self._call(self._cleanup_processed_updates_sync)

    # --------------------------------------------------- ZIP upload gate

    def _gate_row_sync(self) -> dict[str, Any]:
        row = self._fetchone(
            "SELECT state, opened_by, expires_at, token, update_key FROM update_upload_gate "
            "WHERE id=1",
        )
        if row is None:
            return {"state": "disabled", "opened_by": None, "expires_at": None,
                    "token": None, "update_key": None}
        return row

    def _open_update_upload_gate_sync(
        self, opened_by: int, now: float, window_seconds: int,
    ) -> str:
        with self._transaction() as connection:
            current = self._gate_row_sync()
            if str(current["state"]) == "processing":
                return "processing"
            connection.execute(
                "UPDATE update_upload_gate SET state='open', opened_by=?, expires_at=?, "
                "token=NULL, update_key=NULL, updated_at=? WHERE id=1",
                (int(opened_by), float(now) + max(1, int(window_seconds)), self._now()),
            )
        return "open"

    async def open_update_upload_gate(
        self, opened_by: int, now: float, window_seconds: int,
    ) -> str:
        return await self._call(
            self._open_update_upload_gate_sync, opened_by, now, window_seconds,
        )

    def _close_update_upload_gate_sync(self, now: float) -> str:
        with self._transaction() as connection:
            current = self._gate_row_sync()
            if str(current["state"]) == "processing":
                return "processing"
            expired = (
                str(current["state"]) == "open"
                and current["expires_at"] is not None
                and float(current["expires_at"]) <= float(now)
            )
            connection.execute(
                "UPDATE update_upload_gate SET state='disabled', token=NULL, update_key=NULL, "
                "updated_at=? WHERE id=1",
                (self._now(),),
            )
        return "expired" if expired else "disabled"

    async def close_update_upload_gate(self, now: float) -> str:
        return await self._call(self._close_update_upload_gate_sync, now)

    def _get_update_upload_gate_sync(self, now: float) -> dict[str, Any]:
        with self._transaction() as connection:
            current = self._gate_row_sync()
            state = str(current["state"])
            expires_at = current["expires_at"]
            if (
                state == "open" and expires_at is not None
                and float(expires_at) <= float(now)
            ):
                connection.execute(
                    "UPDATE update_upload_gate SET state='disabled', token=NULL, "
                    "update_key=NULL, updated_at=? WHERE id=1",
                    (self._now(),),
                )
                state = "disabled"
        return {"state": state, "expires_at": expires_at}

    async def get_update_upload_gate(self, now: float) -> dict[str, Any]:
        return await self._call(self._get_update_upload_gate_sync, now)

    def _claim_update_upload_gate_sync(
        self, update_key: str, now: float,
    ) -> tuple[str | None, str]:
        with self._transaction() as connection:
            current = self._gate_row_sync()
            state = str(current["state"])
            if state != "open":
                return None, state
            expires_at = current["expires_at"]
            if expires_at is None or float(expires_at) <= float(now):
                connection.execute(
                    "UPDATE update_upload_gate SET state='disabled', token=NULL, "
                    "update_key=NULL, updated_at=? WHERE id=1",
                    (self._now(),),
                )
                return None, "disabled"
            token = uuid.uuid4().hex
            connection.execute(
                "UPDATE update_upload_gate SET state='processing', token=?, update_key=?, "
                "expires_at=NULL, updated_at=? WHERE id=1 AND state='open'",
                (token, str(update_key)[:120], self._now()),
            )
        return token, "processing"

    async def claim_update_upload_gate(
        self, update_key: str, now: float,
    ) -> tuple[str | None, str]:
        return await self._call(self._claim_update_upload_gate_sync, update_key, now)

    def _finish_update_upload_gate_sync(self, token: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE update_upload_gate SET state='disabled', token=NULL, update_key=NULL, "
                "updated_at=? WHERE id=1 AND state='processing' AND token=?",
                (self._now(), str(token)),
            )
            return int(cursor.rowcount or 0) > 0

    async def finish_update_upload_gate(self, token: str) -> bool:
        return await self._call(self._finish_update_upload_gate_sync, token)

    def _recover_update_upload_gate_sync(self, now: float) -> int:
        with self._transaction() as connection:
            current = self._gate_row_sync()
            state = str(current["state"])
            expired = (
                state == "open" and current["expires_at"] is not None
                and float(current["expires_at"]) <= float(now)
            )
            if state != "processing" and not expired:
                return 0
            connection.execute(
                "UPDATE update_upload_gate SET state='disabled', token=NULL, update_key=NULL, "
                "updated_at=? WHERE id=1",
                (self._now(),),
            )
        return 1

    async def recover_update_upload_gate(self, now: float) -> int:
        return await self._call(self._recover_update_upload_gate_sync, now)

    # ------------------------------------------------------- provider files

    def _record_provider_file_sync(self, file_id: str) -> None:
        key = f"provider_file:{hashlib.sha256(str(file_id).encode()).hexdigest()[:32]}"
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?,?,?)",
                (key, str(file_id)[:200], self._now()),
            )

    async def record_provider_file(self, file_id: str) -> None:
        await self._call(self._record_provider_file_sync, file_id)

    def _pending_provider_files_sync(self) -> list[str]:
        return [
            str(row["value"]) for row in self._fetchall(
                "SELECT value FROM settings WHERE key LIKE 'provider_file:%' ORDER BY key",
            )
        ]

    async def pending_provider_files(self) -> list[str]:
        return await self._call(self._pending_provider_files_sync)

    def _clear_provider_file_sync(self, file_id: str) -> None:
        key = f"provider_file:{hashlib.sha256(str(file_id).encode()).hexdigest()[:32]}"
        with self._transaction() as connection:
            connection.execute("DELETE FROM settings WHERE key=?", (key,))

    async def clear_provider_file(self, file_id: str) -> None:
        await self._call(self._clear_provider_file_sync, file_id)

    # ------------------------------------------------------------- stickers

    def _save_sticker_sync(self, **fields: Any) -> bool:
        file_id = str(fields.get("file_id") or "")
        if not file_id:
            raise ValueError("A sticker needs a Telegram file ID")
        category = str(fields.get("category") or "general")
        if category not in STICKER_CATEGORIES:
            category = "general"
        tags = fields.get("tags") or []
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM sticker_registry WHERE file_id=? OR "
                "(file_unique_id IS NOT NULL AND file_unique_id=?)",
                (file_id, fields.get("file_unique_id")),
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                "INSERT OR IGNORE INTO sticker_registry(file_id, file_unique_id, emoji, set_name, "
                "set_title, sticker_type, is_animated, is_video, width, height, category, tags, "
                "source_type, added_by, approved, added_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    file_id, fields.get("file_unique_id") or None,
                    str(fields.get("emoji") or "")[:16], str(fields.get("set_name") or "")[:200],
                    str(fields.get("set_title") or "")[:200],
                    str(fields.get("sticker_type") or "regular")[:32],
                    1 if fields.get("is_animated") else 0, 1 if fields.get("is_video") else 0,
                    int(fields.get("width") or 0), int(fields.get("height") or 0),
                    category, json.dumps([str(tag)[:40] for tag in tags][:20]),
                    str(fields.get("source_type") or "pack_import")[:32],
                    int(fields.get("added_by") or 0), 1 if fields.get("approved") else 0,
                    self._now(),
                ),
            )
        return True

    async def save_sticker(self, **fields: Any) -> bool:
        return await self._call(self._save_sticker_sync, **fields)

    def _search_stickers_sync(
        self, query: str, approved_only: bool, limit: int,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, file_id, file_unique_id, emoji, set_name, set_title, category, "
            "source_type, approved, is_animated, is_video, sticker_type, usage_count "
            "FROM sticker_registry WHERE 1=1"
        )
        params: list[Any] = []
        term = " ".join(str(query).split())
        if term:
            pattern = self._like(term)
            sql += (
                " AND (category LIKE ? ESCAPE '\\' OR emoji LIKE ? ESCAPE '\\' "
                "OR set_name LIKE ? ESCAPE '\\' OR set_title LIKE ? ESCAPE '\\' "
                "OR tags LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 5)
        if approved_only:
            sql += " AND approved=1"
        sql += " ORDER BY approved DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return self._fetchall(sql, params)

    async def search_stickers(
        self, query: str = "", approved_only: bool = False, limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await self._call(self._search_stickers_sync, query, approved_only, limit)

    def _list_stickers_sync(
        self, pending_only: bool, approved_only: bool, limit: int,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, file_id, file_unique_id, emoji, set_name, category, source_type, "
            "approved, is_animated, is_video, sticker_type, usage_count "
            "FROM sticker_registry WHERE 1=1"
        )
        if pending_only:
            sql += " AND approved=0"
        if approved_only:
            sql += " AND approved=1"
        sql += " ORDER BY id DESC LIMIT ?"
        return self._fetchall(sql, (max(1, int(limit)),))

    async def list_stickers(
        self, pending_only: bool = False, approved_only: bool = False, limit: int = 200,
    ) -> list[dict[str, Any]]:
        return await self._call(self._list_stickers_sync, pending_only, approved_only, limit)

    def _sticker_stats_sync(self) -> dict[str, int]:
        row = self._fetchone(
            "SELECT COUNT(*) AS total, COALESCE(SUM(approved),0) AS approved, "
            "COALESCE(SUM(CASE WHEN approved=0 THEN 1 ELSE 0 END),0) AS pending, "
            "COUNT(DISTINCT NULLIF(set_name,'')) AS packs, "
            "COALESCE(SUM(is_animated),0) AS animated, COALESCE(SUM(is_video),0) AS video, "
            "COALESCE(SUM(usage_count),0) AS usage_count FROM sticker_registry",
        ) or {}
        return {
            "total": int(row.get("total") or 0),
            "approved": int(row.get("approved") or 0),
            "pending": int(row.get("pending") or 0),
            "packs": int(row.get("packs") or 0),
            "animated": int(row.get("animated") or 0),
            "video": int(row.get("video") or 0),
            "usage_count": int(row.get("usage_count") or 0),
        }

    async def sticker_stats(self) -> dict[str, int]:
        return await self._call(self._sticker_stats_sync)

    def _update_sticker_category_sync(
        self, sticker_id: int, category: str, tags: Sequence[str],
    ) -> bool:
        selected = str(category).casefold()
        if selected not in STICKER_CATEGORIES:
            return False
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE sticker_registry SET category=?, tags=?, approved=1 WHERE id=?",
                (selected, json.dumps([str(tag)[:40] for tag in tags][:20]), int(sticker_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def update_sticker_category(
        self, sticker_id: int, category: str, tags: Sequence[str] = (),
    ) -> bool:
        return await self._call(self._update_sticker_category_sync, sticker_id, category, tags)

    def _export_sticker_metadata_sync(self) -> list[dict[str, Any]]:
        return [
            {
                "id": int(row["id"]),
                "emoji": str(row["emoji"]),
                "set_name": str(row["set_name"]),
                "set_title": str(row["set_title"]),
                "category": str(row["category"]),
                "sticker_type": str(row["sticker_type"]),
                "is_animated": bool(row["is_animated"]),
                "is_video": bool(row["is_video"]),
                "source_type": str(row["source_type"]),
                "approved": bool(row["approved"]),
                "tags": json.loads(str(row["tags"]) or "[]"),
                "usage_count": int(row["usage_count"]),
                "added_at": str(row["added_at"]),
            }
            for row in self._fetchall(
                "SELECT id, emoji, set_name, set_title, category, sticker_type, is_animated, "
                "is_video, source_type, approved, tags, usage_count, added_at "
                "FROM sticker_registry ORDER BY id",
            )
        ]

    async def export_sticker_metadata(self) -> list[dict[str, Any]]:
        return await self._call(self._export_sticker_metadata_sync)

    def _record_sticker_usage_sync(self, sticker_id: int, user_id: int) -> None:
        now = self._now()
        with self._transaction() as connection:
            connection.execute(
                "UPDATE sticker_registry SET usage_count=usage_count+1, last_used_at=? "
                "WHERE id=?",
                (now, int(sticker_id)),
            )
            connection.execute(
                "INSERT INTO sticker_user_usage(user_id, sticker_id, last_used_at) "
                "VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "sticker_id=excluded.sticker_id, last_used_at=excluded.last_used_at",
                (int(user_id), int(sticker_id), now),
            )

    async def record_sticker_usage(self, sticker_id: int, user_id: int) -> None:
        await self._call(self._record_sticker_usage_sync, sticker_id, user_id)

    def _last_user_sticker_use_sync(self, user_id: int) -> str | None:
        row = self._fetchone(
            "SELECT last_used_at FROM sticker_user_usage WHERE user_id=?", (int(user_id),),
        )
        return None if row is None else str(row["last_used_at"])

    async def last_user_sticker_use(self, user_id: int) -> str | None:
        return await self._call(self._last_user_sticker_use_sync, user_id)

    def _record_sticker_send_failure_sync(self, sticker_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE sticker_registry SET send_failures=send_failures+1 WHERE id=?",
                (int(sticker_id),),
            )

    async def record_sticker_send_failure(self, sticker_id: int) -> None:
        await self._call(self._record_sticker_send_failure_sync, sticker_id)

    # ------------------------------------------------------------ missions

    @staticmethod
    def _mission_fingerprint(task: str) -> str:
        return hashlib.sha256(" ".join(task.casefold().split()).encode("utf-8")).hexdigest()

    def _create_mission_sync(
        self, owner_id: int, chat_id: int, task: str, payload: dict[str, Any],
    ) -> tuple[str, bool]:
        fingerprint = self._mission_fingerprint(task)
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM missions WHERE owner_id=? AND task_fingerprint=? "
                "AND state IN ('planning','running','paused')",
                (int(owner_id), fingerprint),
            ).fetchone()
            if existing is not None:
                return str(existing["id"]), False
            cursor = connection.execute(
                "INSERT INTO missions(owner_id, chat_id, task_text, task_fingerprint, state, "
                "payload, created_at, updated_at, expires_at) "
                "VALUES (?,?,?,?,'planning',?,?,?,?)",
                (int(owner_id), int(chat_id), task[:4000], fingerprint, self._wrap(serialized),
                 self._now(), self._now(), expires),
            )
            return str(cursor.lastrowid or 0), True

    async def create_mission(
        self, owner_id: int, chat_id: int, task: str, payload: dict[str, Any],
    ) -> tuple[str, bool]:
        return await self._call(self._create_mission_sync, owner_id, chat_id, task, payload)

    def _mission_row_sync(
        self, mission_id: int | str, owner_id: int | None = None,
    ) -> dict[str, Any] | None:
        try:
            numeric_id = int(mission_id)
        except (TypeError, ValueError):
            return None
        if owner_id is None:
            row = self._fetchone(
                "SELECT id, owner_id, chat_id, task_text, state, payload, error_code, "
                "created_at, updated_at, expires_at FROM missions WHERE id=?",
                (numeric_id,),
            )
        else:
            row = self._fetchone(
                "SELECT id, owner_id, chat_id, task_text, state, payload, error_code, "
                "created_at, updated_at, expires_at FROM missions WHERE id=? AND owner_id=?",
                (numeric_id, int(owner_id)),
            )
        if row is None:
            return None
        try:
            row["payload"] = json.loads(self._unwrap(row["payload"]) or "{}")
        except json.JSONDecodeError:
            row["payload"] = {}
        row["id"] = str(row["id"])
        return row

    async def get_mission(
        self, mission_id: int | str, owner_id: int | None = None,
    ) -> dict[str, Any] | None:
        return await self._call(self._get_mission_sync, mission_id, owner_id)

    def _get_mission_sync(
        self, mission_id: int | str, owner_id: int | None,
    ) -> dict[str, Any] | None:
        return self._mission_row_sync(mission_id, owner_id)

    def _update_mission_sync(
        self, mission_id: int | str, expected: Iterable[str] | None, state: str | None,
        payload: dict[str, Any] | None, error_code: str | None,
    ) -> bool:
        try:
            numeric_id = int(mission_id)
        except (TypeError, ValueError):
            return False
        with self._transaction() as connection:
            current = self._mission_row_sync(numeric_id)
            if current is None:
                return False
            if expected and str(current["state"]) not in {str(item) for item in expected}:
                return False
            assignments = ["updated_at=?"]
            params: list[Any] = [self._now()]
            if state is not None:
                if str(state) not in MISSION_STATES:
                    raise ValueError("Unsupported mission state")
                assignments.append("state=?")
                params.append(str(state))
                if str(state) in TERMINAL_MISSION_STATES:
                    assignments.append("finished_at=?")
                    params.append(self._now())
            if payload is not None:
                assignments.append("payload=?")
                params.append(self._wrap(json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            if error_code is not None:
                assignments.append("error_code=?")
                params.append(str(error_code)[:120])
            params.append(numeric_id)
            cursor = connection.execute(
                f"UPDATE missions SET {', '.join(assignments)} WHERE id=?", params,
            )
            return int(cursor.rowcount or 0) > 0

    async def update_mission(
        self, mission_id: int | str, *, expected: Iterable[str] | None = None,
        state: str | None = None, payload: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        return await self._call(
            self._update_mission_sync, mission_id, tuple(expected) if expected else None,
            state, payload, error_code,
        )

    def _list_missions_sync(self, owner_id: int, limit: int) -> list[dict[str, Any]]:
        return [
            {**row, "id": str(row["id"])}
            for row in self._fetchall(
                "SELECT id, state, task_text, error_code, created_at, updated_at "
                "FROM missions WHERE owner_id=? ORDER BY id DESC LIMIT ?",
                (int(owner_id), max(1, int(limit))),
            )
        ]

    async def list_missions(self, owner_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return await self._call(self._list_missions_sync, owner_id, limit)

    def _delete_mission_sync(self, mission_id: int | str, owner_id: int) -> bool:
        try:
            numeric_id = int(mission_id)
        except (TypeError, ValueError):
            return False
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM missions WHERE id=? AND owner_id=?", (numeric_id, int(owner_id)),
            )
            return int(cursor.rowcount or 0) > 0

    async def delete_mission(self, mission_id: int | str, owner_id: int) -> bool:
        return await self._call(self._delete_mission_sync, mission_id, owner_id)

    def _initialize_mission_steps_sync(
        self, mission_id: int | str, steps: Sequence[dict[str, Any]],
    ) -> int:
        try:
            numeric_id = int(mission_id)
        except (TypeError, ValueError):
            return 0
        with self._transaction() as connection:
            current = self._mission_row_sync(numeric_id)
            if current is None:
                return 0
            connection.execute("DELETE FROM mission_steps WHERE mission_id=?", (numeric_id,))
            now = self._now()
            for index, step in enumerate(steps):
                title = str((step or {}).get("title", ""))[:240] if isinstance(step, dict) else ""
                connection.execute(
                    "INSERT INTO mission_steps(mission_id, step_index, state, title, updated_at) "
                    "VALUES (?,?,'pending',?,?)",
                    (numeric_id, index, self._wrap(title), now),
                )
            payload = current["payload"] if isinstance(current["payload"], dict) else {}
            payload["step_states"] = ["pending"] * len(steps)
            payload["results"] = payload.get("results") or [{} for _ in steps]
            connection.execute(
                "UPDATE missions SET payload=?, updated_at=? WHERE id=?",
                (self._wrap(json.dumps(payload, ensure_ascii=False, sort_keys=True)), now,
                 numeric_id),
            )
        return len(steps)

    async def initialize_mission_steps(
        self, mission_id: int | str, steps: Sequence[dict[str, Any]],
    ) -> int:
        return await self._call(self._initialize_mission_steps_sync, mission_id, steps)

    def _record_mission_step_sync(
        self, mission_id: int | str, index: int, result: str,
    ) -> tuple[bool, str, int, int]:
        try:
            numeric_id = int(mission_id)
        except (TypeError, ValueError):
            return False, "failed", 0, 0
        with self._transaction() as connection:
            current = self._mission_row_sync(numeric_id)
            if current is None:
                return False, "failed", 0, 0
            state = str(current["state"])
            if state not in {"running", "paused"}:
                return False, state, 0, 0
            payload = current["payload"] if isinstance(current["payload"], dict) else {}
            steps = payload.get("steps") or []
            states = payload.get("step_states") or []
            results = payload.get("results") or []
            while len(states) < len(steps):
                states.append("pending")
            while len(results) < len(steps):
                results.append({})
            if not 0 <= int(index) < len(states):
                return False, state, 0, 0
            states[int(index)] = "completed"
            results[int(index)] = {"text": str(result)[:4000]}
            payload["step_states"] = states
            payload["results"] = results
            now = self._now()
            connection.execute(
                "UPDATE mission_steps SET state='completed', result=?, updated_at=? "
                "WHERE mission_id=? AND step_index=?",
                (self._wrap(str(result)[:4000]), now, numeric_id, int(index)),
            )
            connection.execute(
                "UPDATE missions SET payload=?, updated_at=? WHERE id=?",
                (self._wrap(json.dumps(payload, ensure_ascii=False, sort_keys=True)), now,
                 numeric_id),
            )
        completed = sum(1 for item in states if item == "completed")
        return True, state, completed, len(states)

    async def record_mission_step(
        self, mission_id: int | str, index: int, result: str,
    ) -> tuple[bool, str, int, int]:
        return await self._call(self._record_mission_step_sync, mission_id, index, result)

    def _recover_missions_after_restart_sync(self) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE missions SET state='paused', updated_at=? "
                "WHERE state IN ('planning','running')",
                (self._now(),),
            )
            return int(cursor.rowcount or 0)

    async def recover_missions_after_restart(self) -> int:
        return await self._call(self._recover_missions_after_restart_sync)

    def _expire_missions_sync(self) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE missions SET payload=NULL, updated_at=? "
                "WHERE payload IS NOT NULL AND expires_at<=?",
                (self._now(), self._now()),
            )
            return int(cursor.rowcount or 0)

    async def expire_missions(self) -> int:
        return await self._call(self._expire_missions_sync)

    def _cleanup_mission_history_sync(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM missions WHERE state IN ('completed','cancelled','failed') "
                "AND updated_at<=?",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)

    async def cleanup_mission_history(self) -> int:
        return await self._call(self._cleanup_mission_history_sync)

    # --------------------------------------------------- evolution proposals

    def _create_evolution_proposal_sync(
        self, requester_id: int, summary: str, patch: bytes, archive: bytes,
    ) -> int:
        patch_hash = hashlib.sha256(bytes(patch)).hexdigest()
        source_hash = hashlib.sha256(bytes(archive)).hexdigest()
        expires = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        now = self._now()
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO evolution_proposals(requester_id, summary, patch_hash, source_hash, "
                "state, patch, archive, created_at, updated_at, expires_at) "
                "VALUES (?,?,?,?,'testing',?,?,?,?,?)",
                (int(requester_id), str(summary)[:500], patch_hash, source_hash,
                 self._wrap(bytes(patch).decode("latin-1")), self._wrap(bytes(archive).decode("latin-1")),
                 now, now, expires),
            )
            return int(cursor.lastrowid or 0)

    async def create_evolution_proposal(
        self, requester_id: int, summary: str, patch: bytes, archive: bytes,
    ) -> int:
        return await self._call(
            self._create_evolution_proposal_sync, requester_id, summary, patch, archive,
        )

    def _evolution_row_sync(self, proposal_id: int) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT id, requester_id, summary, patch_hash, source_hash, state, patch, archive, "
            "test_report, test_exit_code, test_passed, test_timed_out, staging_report, "
            "candidate_hash, created_at, updated_at, expires_at "
            "FROM evolution_proposals WHERE id=?",
            (int(proposal_id),),
        )
        if row is None:
            return None
        payload: dict[str, str] = {}
        if row["patch"] is not None:
            payload["patch"] = base64.b64encode(
                self._unwrap(row["patch"]).encode("latin-1"),
            ).decode("ascii")
        if row["archive"] is not None:
            payload["archive"] = base64.b64encode(
                self._unwrap(row["archive"]).encode("latin-1"),
            ).decode("ascii")
        row["payload"] = payload
        row["test_report"] = self._unwrap(row["test_report"])
        row["test_passed"] = (
            None if row["test_passed"] is None else bool(row["test_passed"])
        )
        row["test_timed_out"] = bool(row["test_timed_out"])
        return row

    async def get_evolution_proposal(self, proposal_id: int) -> dict[str, Any] | None:
        return await self._call(self._get_evolution_proposal_sync, proposal_id)

    def _get_evolution_proposal_sync(self, proposal_id: int) -> dict[str, Any] | None:
        return self._evolution_row_sync(proposal_id)

    def _list_evolution_proposals_sync(
        self, limit: int, pending_only: bool,
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, state, patch_hash, summary, created_at, expires_at FROM evolution_proposals"
        params: list[Any] = []
        if pending_only:
            sql += " WHERE state IN ('testing','tested','approved_staging','staging_verified')"
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return self._fetchall(sql, params)

    async def list_evolution_proposals(
        self, limit: int = 20, pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(self._list_evolution_proposals_sync, limit, pending_only)

    def _record_evolution_test_sync(
        self, proposal_id: int, patch_hash: str, output: str,
        exit_code: int, timed_out: bool,
    ) -> bool:
        passed = int(exit_code) == 0 and not timed_out
        with self._transaction() as connection:
            current = self._evolution_row_sync(proposal_id)
            if current is None or str(current["patch_hash"]) != str(patch_hash):
                return False
            if str(current["state"]) not in {"testing", "tested", "test_failed"}:
                return False
            connection.execute(
                "UPDATE evolution_proposals SET state=?, test_report=?, test_exit_code=?, "
                "test_passed=?, test_timed_out=?, updated_at=? WHERE id=?",
                ("tested" if passed else "test_failed", self._wrap(str(output)[:20000]),
                 int(exit_code), 1 if passed else 0, 1 if timed_out else 0, self._now(),
                 int(proposal_id)),
            )
        return True

    async def record_evolution_test(
        self, proposal_id: int, patch_hash: str, output: str = "",
        exit_code: int = 1, timed_out: bool = False,
    ) -> bool:
        return await self._call(
            self._record_evolution_test_sync, proposal_id, patch_hash, output,
            exit_code, timed_out,
        )

    def _record_evolution_staging_sync(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
        output: str, exit_code: int, timed_out: bool, candidate_archive: bytes | None,
    ) -> bool:
        if int(actor_id) not in {int(item) for item in admin_ids}:
            return False
        passed = int(exit_code) == 0 and not timed_out
        with self._transaction() as connection:
            current = self._evolution_row_sync(proposal_id)
            if current is None or str(current["patch_hash"]) != str(patch_hash):
                return False
            if str(current["state"]) != "approved_staging":
                return False
            candidate_hash = (
                hashlib.sha256(bytes(candidate_archive)).hexdigest()
                if candidate_archive else ""
            )
            connection.execute(
                "UPDATE evolution_proposals SET state=?, staging_report=?, test_exit_code=?, "
                "test_passed=?, test_timed_out=?, candidate_hash=?, updated_at=? WHERE id=?",
                ("staging_verified" if passed else "staging_failed",
                 self._wrap(str(output)[:20000]), int(exit_code), 1 if passed else 0,
                 1 if timed_out else 0, candidate_hash, self._now(), int(proposal_id)),
            )
        return True

    async def record_evolution_staging_verification(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
        output: str = "", exit_code: int = 1, timed_out: bool = False,
        candidate_archive: bytes | None = None,
    ) -> bool:
        return await self._call(
            self._record_evolution_staging_sync, proposal_id, patch_hash, actor_id,
            tuple(admin_ids), output, exit_code, timed_out, candidate_archive,
        )

    def _decide_evolution_staging_sync(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
        approve: bool,
    ) -> bool:
        if int(actor_id) not in {int(item) for item in admin_ids}:
            return False
        with self._transaction() as connection:
            current = self._evolution_row_sync(proposal_id)
            if current is None or str(current["patch_hash"]) != str(patch_hash):
                return False
            if str(current["state"]) != "tested" or not current["test_passed"]:
                return False
            connection.execute(
                "UPDATE evolution_proposals SET state=?, updated_at=? WHERE id=?",
                ("approved_staging" if approve else "cancelled", self._now(), int(proposal_id)),
            )
        return True

    async def decide_evolution_staging(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
        approve: bool = True,
    ) -> bool:
        return await self._call(
            self._decide_evolution_staging_sync, proposal_id, patch_hash, actor_id,
            tuple(admin_ids), approve,
        )

    def _approve_evolution_deployment_sync(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
    ) -> bool:
        if int(actor_id) not in {int(item) for item in admin_ids}:
            return False
        with self._transaction() as connection:
            current = self._evolution_row_sync(proposal_id)
            if current is None or str(current["patch_hash"]) != str(patch_hash):
                return False
            if str(current["state"]) != "staging_verified" or not current["test_passed"]:
                return False
            connection.execute(
                "UPDATE evolution_proposals SET state='deployment_approved', updated_at=? "
                "WHERE id=?",
                (self._now(), int(proposal_id)),
            )
        return True

    async def approve_evolution_deployment(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
    ) -> bool:
        return await self._call(
            self._approve_evolution_deployment_sync, proposal_id, patch_hash, actor_id,
            tuple(admin_ids),
        )

    def _cancel_evolution_proposal_sync(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
    ) -> bool:
        if int(actor_id) not in {int(item) for item in admin_ids}:
            return False
        with self._transaction() as connection:
            current = self._evolution_row_sync(proposal_id)
            if current is None or str(current["patch_hash"]) != str(patch_hash):
                return False
            if str(current["state"]) in {"deployment_approved", "cancelled"}:
                return False
            connection.execute(
                "UPDATE evolution_proposals SET state='cancelled', patch=NULL, archive=NULL, "
                "test_report=NULL, staging_report=NULL, updated_at=? WHERE id=?",
                (self._now(), int(proposal_id)),
            )
        return True

    async def cancel_evolution_proposal(
        self, proposal_id: int, patch_hash: str, actor_id: int, admin_ids: Iterable[int],
    ) -> bool:
        return await self._call(
            self._cancel_evolution_proposal_sync, proposal_id, patch_hash, actor_id,
            tuple(admin_ids),
        )

    def _expire_evolution_proposals_sync(self) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE evolution_proposals SET patch=NULL, archive=NULL, test_report=NULL, "
                "staging_report=NULL, updated_at=? WHERE patch IS NOT NULL AND expires_at<=?",
                (self._now(), self._now()),
            )
            return int(cursor.rowcount or 0)

    async def expire_evolution_proposals(self) -> int:
        return await self._call(self._expire_evolution_proposals_sync)

    def _cleanup_evolution_history_sync(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM evolution_proposals WHERE state IN "
                "('deployment_approved','cancelled','test_failed','staging_failed') "
                "AND updated_at<=?",
                (cutoff,),
            )
            return int(cursor.rowcount or 0)

    async def cleanup_evolution_history(self) -> int:
        return await self._call(self._cleanup_evolution_history_sync)

    # ------------------------------------------------- export and deletion

    def _export_user_data_sync(self, user_id: int, private_chat_id: int) -> dict[str, Any]:
        owner = int(user_id)
        chat = int(private_chat_id)
        export: dict[str, Any] = {
            "user_id": owner,
            "exported_at": self._now(),
            "preferences": {
                str(row["key"]): str(row["value"]) for row in self._fetchall(
                    "SELECT key, value FROM user_preferences WHERE user_id=?", (owner,),
                )
            },
            "memories": self._memories_sync(owner),
            "profile_facts": self._get_user_facts_sync(owner),
            "learned_knowledge": self._get_learned_knowledge_sync(owner),
            "reminders": self._list_reminders_sync(owner),
            "tasks": self._task_rows_sync(owner),
            "study_cards": self._list_study_cards_sync(owner, False),
            "usage": self._usage_today_sync(owner),
            "feedback": self._fetchall(
                "SELECT rating, category, created_at FROM feedback WHERE user_id=? ORDER BY id",
                (owner,),
            ),
            "missions": self._fetchall(
                "SELECT id, state, error_code, created_at, updated_at FROM missions "
                "WHERE owner_id=? ORDER BY id",
                (owner,),
            ),
            "evolution_proposals": self._fetchall(
                "SELECT id, state, patch_hash, summary, created_at FROM evolution_proposals "
                "WHERE requester_id=? ORDER BY id",
                (owner,),
            ),
            "sticker_usage": self._fetchall(
                "SELECT sticker_id, last_used_at FROM sticker_user_usage WHERE user_id=?",
                (owner,),
            ),
            "agent_jobs": self._fetchall(
                "SELECT id, task_type, status, step_count, cost_usd, created_at "
                "FROM agent_jobs WHERE user_id=? ORDER BY id",
                (owner,),
            ),
            "kb_documents": self._list_kb_documents_sync(owner),
            "kb_content": [
                {
                    "document_id": int(row["document_id"]),
                    "source": self._unwrap(row["source"]),
                    "content": self._unwrap(row["content"]),
                }
                for row in self._fetchall(
                    "SELECT c.document_id, c.source, c.content FROM kb_chunks c "
                    "JOIN kb_documents d ON d.id=c.document_id WHERE d.user_id=? ORDER BY c.id",
                    (owner,),
                )
            ],
            "approvals": self._fetchall(
                "SELECT id, kind, summary, required_approver, status, created_at, expires_at "
                "FROM approvals WHERE requester_id=? ORDER BY id",
                (owner,),
            ),
            "audit_events": self._fetchall(
                "SELECT id, action, target, outcome, created_at FROM audit_events "
                "WHERE actor_id=? ORDER BY id",
                (owner,),
            ),
            "moderation": self._fetchall(
                "SELECT user_id, reason, blocked_at FROM blocked_users WHERE user_id=?",
                (owner,),
            ),
        }
        if chat == owner:
            export["messages"] = self._history_sync(chat, 1000)
            export["documents"] = self._fetchall(
                "SELECT d.id, d.filename, d.mime_type, d.created_at, d.expires_at, "
                "c.source, c.content FROM documents d JOIN document_chunks c "
                "ON c.document_id=d.id WHERE d.chat_id=? ORDER BY d.id, c.chunk_index",
                (chat,),
            )
        return export

    async def export_user_data(self, user_id: int, private_chat_id: int) -> dict[str, Any]:
        return await self._call(self._export_user_data_sync, user_id, private_chat_id)

    def _delete_user_data_sync(self, user_id: int, private_chat_id: int) -> None:
        owner = int(user_id)
        chat = int(private_chat_id)
        with self._transaction() as connection:
            for table, column in (
                ("user_preferences", "user_id"), ("memories", "user_id"),
                ("user_memories", "user_id"), ("learned_knowledge", "user_id"),
                ("reminders", "user_id"), ("user_tasks", "user_id"),
                ("study_cards", "user_id"), ("usage_events", "user_id"),
                ("feedback", "user_id"), ("agent_jobs", "user_id"),
                ("kb_documents", "user_id"), ("missions", "owner_id"),
                ("evolution_proposals", "requester_id"), ("approvals", "requester_id"),
                ("sticker_user_usage", "user_id"),
            ):
                connection.execute(f"DELETE FROM {table} WHERE {column}=?", (owner,))
            connection.execute("DELETE FROM audit_events WHERE actor_id=?", (owner,))
            self._rechain_audit(connection)
            if chat == owner:
                connection.execute("DELETE FROM messages WHERE chat_id=?", (chat,))
                connection.execute("DELETE FROM documents WHERE chat_id=?", (chat,))
                connection.execute("DELETE FROM action_executions WHERE chat_id=?", (chat,))
                connection.execute(
                    "DELETE FROM admin_reply_map WHERE user_chat_id=? OR user_id=?", (chat, owner),
                )
        # blocked_users rows are intentionally retained to keep the abuse block in place.

    async def delete_user_data(self, user_id: int, private_chat_id: int) -> None:
        await self._call(self._delete_user_data_sync, user_id, private_chat_id)

    # -------------------------------------------------------------- backups

    async def backup_to(self, destination: str | Path) -> Path:
        return await self._call(self._backup_to_sync, Path(destination))

    def _backup_to_sync(self, destination: Path) -> Path:
        self._require_connection()
        target = Path(destination)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = sqlite3.connect(self.path, timeout=5.0)
        target_connection = sqlite3.connect(target, timeout=5.0)
        try:
            source.backup(target_connection)
            integrity = target_connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("SQLite integrity check failed for the new backup")
        finally:
            target_connection.close()
            source.close()
        if os.name != "nt":
            try:
                target.chmod(0o600)
            except OSError:
                pass
        return target

    async def backup_encrypted_to(self, destination: str | Path) -> Path:
        return await self._call(self._backup_encrypted_to_sync, Path(destination))

    def _backup_encrypted_to_sync(self, destination: Path) -> Path:
        if self._cipher is None:
            raise ValueError("APP_DATA_KEY is required to encrypt database backups")
        target = Path(destination)
        snapshot = target.with_name(f".{target.name}.snapshot")
        try:
            self._backup_to_sync(snapshot)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(self._cipher.encrypt(snapshot.read_bytes()))
            if os.name != "nt":
                target.chmod(0o600)
        finally:
            snapshot.unlink(missing_ok=True)
        return target
