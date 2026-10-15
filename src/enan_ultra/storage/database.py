"""Async SQLite persistence with versioned, idempotent migrations."""

from __future__ import annotations

import asyncio
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
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at DESC);
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
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                fact TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, id DESC);
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
        data["reminders"] = await rows(
            "SELECT id,reminder_text,run_at,repeat_seconds,delivered,created_at "
            "FROM reminders WHERE user_id=? ORDER BY id",
            (user_id,),
        )
        data["tasks"] = await rows(
            "SELECT id,task_text,status,priority,due_at,created_at,updated_at FROM user_tasks "
            "WHERE user_id=? ORDER BY id",
            (user_id,),
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
                    "user_preferences", "memories", "reminders", "user_tasks", "usage_events",
                    "study_cards", "feedback", "agent_jobs", "kb_documents",
                )
            ),
            f"DELETE FROM approvals WHERE requester_id={user_id};",
            f"DELETE FROM live_sessions WHERE user_id={user_id};",
            f"DELETE FROM audit_events WHERE actor_id={user_id};",
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

    async def map_admin_message(self, message_id: int, user_chat_id: int) -> None:
        db = self._db()
        await db.execute(
            "INSERT OR REPLACE INTO admin_reply_map(admin_message_id,user_chat_id,created_at) "
            "VALUES(?,?,?)",
            (message_id, user_chat_id, utc_now().isoformat()),
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

    async def forget_memory(self, user_id: int, memory_id: int | None = None) -> int:
        if memory_id is None:
            cursor = await self._db().execute("DELETE FROM memories WHERE user_id=?", (user_id,))
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

    async def record_feedback(self, user_id: int, rating: int, category: str = "") -> None:
        if rating not in {-1, 1}:
            raise ValueError("Feedback rating must be -1 or 1")
        category = " ".join("".join(char for char in category if char.isprintable()).split())[:40]
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
    ) -> int:
        if priority not in {"low", "normal", "high", "urgent"}:
            raise ValueError("Invalid task priority")
        if due_at is not None:
            try:
                date.fromisoformat(due_at)
            except ValueError as exc:
                raise ValueError("Task deadline must be an ISO date") from exc
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS n FROM user_tasks WHERE user_id=? AND status='open'",
            (user_id,),
        )
        current = await cursor.fetchone()
        await cursor.close()
        if int(current["n"]) >= 100:
            raise ValueError("Open task limit reached")
        now = utc_now().isoformat()
        cursor = await self._db().execute(
            "INSERT INTO user_tasks(user_id,task_text,priority,due_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, text[:500], priority, due_at, now, now),
        )
        await self._db().commit()
        return int(cursor.lastrowid or 0)

    async def list_tasks(self, user_id: int) -> list[dict[str, Any]]:
        cursor = await self._db().execute(
            "SELECT id,task_text,status,priority,due_at,created_at,updated_at FROM user_tasks "
            "WHERE user_id=? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,"
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,"
            "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,id DESC LIMIT 100",
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def complete_task(self, user_id: int, task_id: int) -> bool:
        cursor = await self._db().execute(
            "UPDATE user_tasks SET status='done',updated_at=? "
            "WHERE id=? AND user_id=? AND status='open'",
            (utc_now().isoformat(), task_id, user_id),
        )
        changed = cursor.rowcount == 1
        await cursor.close()
        await self._db().commit()
        return changed

    async def delete_task(self, user_id: int, task_id: int) -> bool:
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
