from __future__ import annotations

import asyncio
import io
import sqlite3
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from enan_ultra.crypto import DataCipher
from enan_ultra.config import Settings
from enan_ultra.dashboard import AdminDashboard
from enan_ultra.models import AssistantResult
from enan_ultra.services.code_sandbox import extract_project_zip
from enan_ultra.files.registry import AudioAdapter, ImageAdapter
from enan_ultra.services.openrouter import ProviderError
from enan_ultra.services.providers import OpenAICompatibleAdapter, ProviderRouter
from enan_ultra.services.key_rotation import KeyRotation
from enan_ultra.storage.database import Database


KEY = "test-only-app-data-key-with-sufficient-entropy-123456789"


def test_data_cipher_authenticates_and_roundtrips() -> None:
    cipher = DataCipher(KEY)
    encrypted = cipher.encrypt("private Bengali তথ্য")
    assert b"private Bengali" not in encrypted
    assert cipher.decrypt_text(encrypted) == "private Bengali তথ্য"
    with pytest.raises(ValueError):
        DataCipher("wrong-test-app-data-key-with-sufficient-entropy").decrypt_text(encrypted)


def test_personal_kb_is_encrypted_isolated_and_deletable(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "assistant.sqlite3"
        db = Database(path, data_key=KEY, kb_max_documents=1)
        await db.open()
        try:
            source_id = await db.save_document(
                chat_id=900, filename="private.txt", mime_type="text/plain",
                chunks=[("line 4", "private Orchid launch code")],
            )
            kb_id = await db.promote_document_to_kb(user_id=11, chat_id=900, document_id=source_id)
            assert (await db.search_kb(11, "Orchid"))[0]["content"] == "private Orchid launch code"
            assert await db.search_kb(12, "Orchid") == []
            rows = await db._db().execute_fetchall(
                "SELECT filename,mime_type FROM kb_documents WHERE id=?", (kb_id,)
            )
            chunks = await db._db().execute_fetchall(
                "SELECT source,content FROM kb_chunks WHERE document_id=?", (kb_id,)
            )
            for value in (*rows[0], *chunks[0]):
                assert b"Orchid" not in bytes(value)
            second_id = await db.save_document(900, "more.txt", "text/plain", [("line 1", "another document")])
            with pytest.raises(ValueError, match="cap is"):
                await db.promote_document_to_kb(11, 900, second_id)
            assert await db.delete_kb_document(12, kb_id) == 0
            assert await db.delete_kb_document(11, kb_id) == 1
            assert await db.search_kb(11, "Orchid") == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_approval_is_encrypted_authorized_and_one_shot(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            approval_id = await db.create_approval(
                requester_id=77, kind="home_assistant_action", summary="Turn on a light",
                payload={"entity_ids": ["light.office"], "secret_note": "private-action-payload"},
                required_approver="admin",
            )
            cursor = await db._db().execute("SELECT payload FROM approvals WHERE id=?", (approval_id,))
            payload_row = await cursor.fetchone()
            await cursor.close()
            assert b"private-action-payload" not in bytes(payload_row[0])
            assert await db.consume_approval(approval_id, 77, frozenset({10})) is None
            consumed = await db.consume_approval(approval_id, 10, frozenset({10}))
            assert consumed and consumed["payload"]["secret_note"] == "private-action-payload"
            assert await db.consume_approval(approval_id, 10, frozenset({10})) is None
            await db.finish_approval(approval_id, "approved", "done")
            cursor = await db._db().execute("SELECT payload FROM approvals WHERE id=?", (approval_id,))
            assert DataCipher(KEY).decrypt_text((await cursor.fetchone())[0]) == "{}"
            await cursor.close()

            requester_approval = await db.create_approval(77, "code_test", "Run tests", {"safe": True}, "requester")
            assert await db.consume_approval(requester_approval, 10, frozenset({10})) is None
            assert await db.consume_approval(requester_approval, 77, frozenset({10})) is not None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_expired_approval_cannot_be_consumed(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            approval_id = await db.create_approval(77, "vm_restart", "Restart", {}, "admin")
            await db._db().execute("UPDATE approvals SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (approval_id,))
            await db._db().commit()
            assert await db.consume_approval(approval_id, 10, frozenset({10})) is None
            assert await db.expire_approvals() == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_tool_action_claim_is_one_shot_per_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            assert await db.claim_action_execution(10, 50, "same-action", "send_poll")
            assert not await db.claim_action_execution(10, 50, "same-action", "send_poll")
            assert await db.claim_action_execution(10, 51, "same-action", "send_poll")
            await db.finish_action_execution(10, 50, "same-action", "complete", "poll sent")
            rows = await db._db().execute_fetchall(
                "SELECT status,result FROM action_executions WHERE chat_id=10 AND message_id=50"
            )
            assert tuple(rows[0]) == ("complete", "poll sent")
            versions = await db._db().execute_fetchall("SELECT version FROM schema_version ORDER BY version")
            assert [row[0] for row in versions][-1] == 8
        finally:
            await db.close()

    asyncio.run(scenario())


def test_recurring_reminders_and_user_data_export_delete(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    async def scenario() -> None:
        path = tmp_path / "assistant.sqlite3"
        db = Database(path, data_key=KEY)
        await db.open()
        try:
            user_id = 77
            await db.set_preference(user_id, "language", "bn")
            await db.add_memory(user_id, "পছন্দের ভাষা বাংলা")
            reminder_id = await db.add_reminder(
                user_id, user_id, "প্রতিদিন পানি পান", datetime.now(UTC) - timedelta(days=4),
                repeat_seconds=86400,
            )
            assert await db.snooze_reminder(user_id, reminder_id, 600)
            assert not await db.snooze_reminder(88, reminder_id, 600)
            snoozed = await db.list_reminders(user_id)
            assert datetime.fromisoformat(snoozed[0]["run_at"]) > datetime.now(UTC)
            task_id = await db.add_task(user_id, "finish roadmap data export")
            priority_task_id = await db.add_task(
                user_id, "prepare high priority report", priority="urgent",
                due_at=datetime.now(UTC).date().isoformat(),
            )
            ordered_tasks = await db.list_tasks(user_id)
            assert ordered_tasks[0]["id"] == priority_task_id
            assert ordered_tasks[0]["priority"] == "urgent"
            assert ordered_tasks[0]["due_at"] == datetime.now(UTC).date().isoformat()
            other_task_id = await db.add_task(88, "separate user's task")
            assert await db.complete_task(user_id, task_id)
            assert not await db.complete_task(88, task_id)
            assert await db.delete_task(88, other_task_id)
            retained_task_id = await db.add_task(88, "keep account separation")
            card_id = await db.add_study_card(user_id, "What is WAL?", "Write-ahead logging")
            other_card_id = await db.add_study_card(88, "Private question", "Private answer")
            assert not await db.review_study_card(88, card_id, remembered=True)
            assert [card["id"] for card in await db.list_study_cards(user_id, due_only=True)] == [card_id]
            assert await db.review_study_card(user_id, card_id, remembered=True)
            card = (await db.list_study_cards(user_id))[0]
            assert card["interval_days"] == 1
            assert card["id"] not in {
                item["id"] for item in await db.list_study_cards(user_id, due_only=True)
            }
            assert await db.review_study_card(user_id, card_id, remembered=False)
            assert (await db.list_study_cards(user_id))[0]["interval_days"] == 0
            assert card_id in {
                card["id"] for card in await db.list_study_cards(user_id, due_only=True)
            }
            assert await db.review_study_card(user_id, card_id, remembered=True)
            assert await db.review_study_card(user_id, card_id, remembered=True)
            assert (await db.list_study_cards(user_id))[0]["interval_days"] == 2
            assert await db.delete_study_card(88, other_card_id)
            document_id = await db.save_document(
                user_id, "private.txt", "text/plain", [("line 1", "private document contents")],
            )
            await db.promote_document_to_kb(user_id, user_id, document_id)
            await db.save_message(user_id, "user", "private conversation")
            await db.save_message(900, "user", "shared group conversation")
            assert (await db.search_history(user_id, "private"))[0]["content"] == "private conversation"
            assert await db.search_history(900, "private") == []
            assert await db.search_history(user_id, "%") == []

            exported = await db.export_user_data(user_id, user_id)
            assert exported["preferences"][0]["value"] == "bn"
            assert exported["memories"][0]["fact"] == "পছন্দের ভাষা বাংলা"
            assert exported["reminders"][0]["repeat_seconds"] == 86400
            assert exported["tasks"][0]["task_text"] == "finish roadmap data export"
            assert exported["tasks"][0]["status"] == "done"
            assert exported["study_cards"][0]["question"] == "What is WAL?"
            assert exported["knowledge_base"][0]["chunks"][0]["content"] == "private document contents"
            assert exported["private_chat_history"][0]["content"] == "private conversation"
            assert exported["temporary_documents"][0]["chunks"][0]["content"] == "private document contents"
            assert "private_chat_history" not in await db.export_user_data(user_id)

            await db._db().execute(
                "UPDATE reminders SET run_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), reminder_id),
            )
            await db._db().commit()
            due = await db.claim_due_reminders()
            assert reminder_id in {item["id"] for item in due}
            assert await db.claim_due_reminders() == []
            assert await db.defer_reminder(reminder_id, 60)
            assert await db.claim_due_reminders() == []
            await db._db().execute(
                "UPDATE reminders SET claimed_until=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), reminder_id),
            )
            await db._db().commit()
            retried = await db.claim_due_reminders()
            assert retried[0]["attempts"] == 2
            assert await db.set_reminder_paused(user_id, reminder_id, paused=True)
            assert await db.claim_due_reminders() == []
            assert await db.set_reminder_paused(user_id, reminder_id, paused=False)
            await db.mark_reminder_delivered(reminder_id)
            listed = await db.list_reminders(user_id)
            assert len(listed) == 1
            assert listed[0]["repeat_seconds"] == 86400
            assert datetime.fromisoformat(listed[0]["run_at"]) > datetime.now(UTC)

            await db.set_user_blocked(user_id, 10, "abuse")
            assert await db.is_user_blocked(user_id)
            assert await db.set_user_unblocked(user_id, 10)
            assert not await db.is_user_blocked(user_id)
            await db.set_user_blocked(user_id, 10, "remove with account")
            await db.audit(user_id, "test.export", "test", "sensitive")

            await db.delete_user_data(user_id, user_id)
            remaining = await db._db().execute_fetchall(
                "SELECT COUNT(*) FROM user_preferences WHERE user_id=?", (user_id,),
            )
            assert remaining[0][0] == 0
            assert await db.memories(user_id) == []
            assert await db.list_reminders(user_id) == []
            assert await db.list_tasks(user_id) == []
            assert await db.list_study_cards(user_id) == []
            assert len(await db.list_study_cards(88)) == 0
            assert [task["id"] for task in await db.list_tasks(88)] == [retained_task_id]
            assert await db.list_kb_documents(user_id) == []
            assert await db.is_user_blocked(user_id)
            assert await db.history(user_id, 10) == []
            assert await db.history(900, 10) == [{"role": "user", "content": "shared group conversation"}]
            audit_rows = await db._db().execute_fetchall(
                "SELECT COUNT(*) FROM audit_events WHERE actor_id=?", (user_id,),
            )
            assert audit_rows[0][0] == 0
        finally:
            await db.close()

        reopened = Database(path, data_key=KEY)
        await reopened.open()
        try:
            versions = await reopened._db().execute_fetchall(
                "SELECT version FROM schema_version ORDER BY version",
            )
            assert [row[0] for row in versions][-1] == 8
            columns = await reopened._db().execute_fetchall("PRAGMA table_info(reminders)")
            assert "repeat_seconds" in {row["name"] for row in columns}
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_duplicate_model_tool_call_only_sends_one_poll(tmp_path: Path) -> None:
    from enan_ultra.actions import ActionContext, ActionRegistry

    async def scenario() -> None:
        class Message:
            message_id = 50

            def __init__(self):
                self.polls = 0

            async def reply_poll(self, *_args, **_kwargs):
                self.polls += 1

        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            message = Message()
            update = SimpleNamespace(
                effective_message=message, effective_chat=SimpleNamespace(id=10),
                effective_user=SimpleNamespace(id=11),
            )
            context = ActionContext(update, SimpleNamespace(), True, None, db)
            registry = ActionRegistry()
            arguments = '{"question":"Choose?","options":["A","B"]}'
            first = await registry.execute("send_poll", arguments, context)
            duplicate = await registry.execute("send_poll", arguments, context)
            assert first.ok
            assert not duplicate.ok and "already handled" in duplicate.message
            assert message.polls == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def test_database_backup_restores_encrypted_kb_with_same_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        source_path = tmp_path / "live.sqlite3"
        backup_path = tmp_path / "private-backups" / "assistant.sqlite3"
        encrypted_backup = tmp_path / "private-backups" / "assistant.sqlite3.enc"
        source = Database(source_path, data_key=KEY)
        await source.open()
        try:
            document_id = await source.save_document(
                500, "plan.txt", "text/plain", [("page 1", "restoreable knowledge")],
            )
            await source.promote_document_to_kb(9, 500, document_id)
            await source.backup_to(backup_path)
            await source.backup_encrypted_to(encrypted_backup)
        finally:
            await source.close()
        restored = Database(backup_path, data_key=KEY)
        await restored.open()
        try:
            assert (await restored.search_kb(9, "restoreable"))[0]["content"] == "restoreable knowledge"
            assert await restored.search_kb(10, "restoreable") == []
        finally:
            await restored.close()
        encrypted_bytes = encrypted_backup.read_bytes()
        assert not encrypted_bytes.startswith(b"SQLite format 3\x00")
        decrypted_path = tmp_path / "decrypted.sqlite3"
        decrypted_path.write_bytes(DataCipher(KEY).decrypt(encrypted_bytes))
        encrypted_restored = Database(decrypted_path, data_key=KEY)
        await encrypted_restored.open()
        try:
            assert (await encrypted_restored.search_kb(9, "restoreable"))[0]["content"] == "restoreable knowledge"
        finally:
            await encrypted_restored.close()

    asyncio.run(scenario())


def test_backup_cli_encryption_and_legacy_restore_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runpy

    backup_cli = runpy.run_path("scripts/database_backup.py")
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE private_data(value TEXT)")
    connection.execute("INSERT INTO private_data VALUES('sensitive record')")
    connection.commit()
    connection.close()
    encrypted = tmp_path / "backup.sqlite3.enc"
    monkeypatch.setenv("DATABASE_PATH", str(source))
    monkeypatch.setenv("APP_DATA_KEY", KEY)
    monkeypatch.setattr(sys, "argv", ["database_backup.py", "backup", str(encrypted)])
    assert backup_cli["main"]() == 0
    assert not encrypted.read_bytes().startswith(b"SQLite format 3\x00")
    restored_path = tmp_path / "restored.sqlite3"
    monkeypatch.setattr(
        sys, "argv",
        ["database_backup.py", "restore", str(encrypted), "--to", str(restored_path)],
    )
    assert backup_cli["main"]() == 0
    restored = sqlite3.connect(restored_path)
    assert restored.execute("SELECT value FROM private_data").fetchone()[0] == "sensitive record"
    restored.close()

    protected_path = tmp_path / "protected.sqlite3"
    protected = sqlite3.connect(protected_path)
    protected.execute("CREATE TABLE sentinel(value TEXT)")
    protected.execute("INSERT INTO sentinel VALUES('keep original')")
    protected.commit()
    protected.close()
    monkeypatch.setenv("APP_DATA_KEY", "incorrect-test-key-with-sufficient-length-123456")
    monkeypatch.setattr(
        sys, "argv",
        ["database_backup.py", "restore", str(encrypted), "--to", str(protected_path), "--replace"],
    )
    assert backup_cli["main"]() == 2
    protected = sqlite3.connect(protected_path)
    assert protected.execute("SELECT value FROM sentinel").fetchone()[0] == "keep original"
    protected.close()

    monkeypatch.setenv("APP_DATA_KEY", KEY)
    legacy_path = tmp_path / "legacy.sqlite3"
    legacy_path.write_bytes(source.read_bytes())
    rejected_target = tmp_path / "rejected.sqlite3"
    monkeypatch.setattr(
        sys, "argv",
        ["database_backup.py", "restore", str(legacy_path), "--to", str(rejected_target)],
    )
    assert backup_cli["main"]() == 2
    assert not rejected_target.exists()


def test_runtime_schedules_encrypted_backup_once_per_day(tmp_path: Path) -> None:
    from enan_ultra.runtime import _scheduled_backup

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            await db.set_preference(21, "language", "bn")
            backup_dir = tmp_path / "backups"
            backup_dir.mkdir()
            for day in range(1, 9):
                (backup_dir / f"assistant-202601{day:02d}T000000Z.sqlite3.enc").write_bytes(b"old")
            decoy = backup_dir / "do-not-delete.sqlite3.enc"
            decoy.write_bytes(b"keep")
            await _scheduled_backup(
                SimpleNamespace(), db, SimpleNamespace(admin_alerts=False),
            )
            backups = list(backup_dir.glob("assistant-*.sqlite3.enc"))
            assert len(backups) == 7
            newest = max(backups, key=lambda path: path.name)
            assert DataCipher(KEY).decrypt(newest.read_bytes()).startswith(b"SQLite format 3\x00")
            assert decoy.read_bytes() == b"keep"
            await _scheduled_backup(
                SimpleNamespace(), db, SimpleNamespace(admin_alerts=False),
            )
            assert len(list(backup_dir.glob("assistant-*.sqlite3.enc"))) == 7
        finally:
            await db.close()

    asyncio.run(scenario())


def test_privacy_export_is_private_and_available_to_blocked_users() -> None:
    from enan_ultra.handlers import BotHandlers

    class FakeDatabase:
        exported_for: int | None = None

        async def is_user_blocked(self, _user_id: int) -> bool:
            return True

        async def export_user_data(self, user_id: int, private_chat_id: int | None):
            self.exported_for = private_chat_id
            return {"user_id": user_id}

    class FakeMessage:
        def __init__(self):
            self.replies: list[str] = []
            self.documents = 0

        async def reply_text(self, text: str):
            self.replies.append(text)

        async def reply_document(self, **_kwargs):
            self.documents += 1

    async def scenario() -> None:
        settings = SimpleNamespace(
            effective_admin_user_ids=frozenset(), access_mode="public", admin_chat_id=-100,
            max_upload_bytes=100_000,
        )
        db = FakeDatabase()
        handlers = BotHandlers(settings, db, None, None, None, None)
        message = FakeMessage()
        group_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42), effective_message=message,
            effective_chat=SimpleNamespace(id=-200, type="group"),
        )
        await handlers.privacy(group_update, SimpleNamespace(args=["export"]))
        assert db.exported_for is None
        assert message.documents == 0
        assert "private message" in message.replies[0]

        private_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42), effective_message=message,
            effective_chat=SimpleNamespace(id=42, type="private"),
        )
        await handlers.privacy(private_update, SimpleNamespace(args=["export"]))
        assert db.exported_for == 42
        assert message.documents == 1

    asyncio.run(scenario())


def test_study_flashcards_are_owner_scoped_and_private(tmp_path: Path) -> None:
    from enan_ultra.handlers import BotHandlers

    class Message:
        def __init__(self):
            self.replies: list[str] = []

        async def reply_text(self, text: str):
            self.replies.append(text)

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            settings = SimpleNamespace(
                effective_admin_user_ids=frozenset(), access_mode="public", admin_chat_id=-100,
            )
            handlers = BotHandlers(settings, db, None, None, None, None)
            message = Message()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=17, full_name="Student"),
                effective_chat=SimpleNamespace(id=17, type="private"),
                effective_message=message,
            )
            await handlers.study(update, SimpleNamespace(args=["card", "add", "Capital", "::", "Dhaka"]))
            card_id = (await db.list_study_cards(17))[0]["id"]
            assert (await db.list_study_cards(17))[0]["question"] == "Capital"
            await handlers.study(update, SimpleNamespace(args=["review", str(card_id), "good"]))
            assert (await db.list_study_cards(17))[0]["interval_days"] == 1

            group_update = SimpleNamespace(
                effective_user=update.effective_user,
                effective_chat=SimpleNamespace(id=-200, type="group"),
                effective_message=message,
            )
            await handlers.study(group_update, SimpleNamespace(args=["cards"]))
            assert "private message" in message.replies[-1]
            assert len(await db.list_study_cards(17)) == 1
            assert await db.list_study_cards(18) == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_translation_and_quiz_follow_usage_limits_and_profile_privacy(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    from enan_ultra.handlers import BotHandlers

    class Message:
        def __init__(self):
            self.replies: list[str] = []

        async def reply_text(self, text: str):
            self.replies.append(text)

    class FakeRouter:
        calls: list[dict] = []

        def default_model(self):
            return "test-model"

        async def estimate_cost(self, *_args, **_kwargs):
            return 0.01

        async def complete(self, messages, **_kwargs):
            self.calls.append({"messages": messages, **_kwargs})
            text = (
                '{"question":"What is photosynthesis?","options":["Making food using light",'
                '"Breaking down rock"],"correct_index":0,"explanation":"Plants convert light into energy."}'
                if "multiple-choice question" in self.calls[-1]["messages"][0]["content"].lower()
                else "generated result"
            )
            return SimpleNamespace(text=text, model="test-model", usage={"cost": 0.01})

    class FakeBot:
        def __init__(self):
            self.send_poll = AsyncMock(return_value=SimpleNamespace(message_id=42))

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            settings = SimpleNamespace(
                effective_admin_user_ids=frozenset(), access_mode="public", admin_chat_id=-100,
                cooldown_seconds=0, user_daily_request_limit=10,
                max_daily_cost_usd=1.0, user_monthly_request_limit=20,
                user_monthly_cost_limit_usd=5.0, max_input_chars=2000,
                max_output_chars=4000,
            )
            router = FakeRouter()
            handlers = BotHandlers(settings, db, router, None, None, None)
            bot = FakeBot()
            handlers._installing_app = SimpleNamespace(bot=bot)
            handlers._profile = AsyncMock(return_value=SimpleNamespace(local_only=True))
            message = Message()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=34, full_name="Learner"),
                effective_chat=SimpleNamespace(id=34, type="private"),
                effective_message=message,
            )
            await handlers.translate(update, SimpleNamespace(args=["bn", "Hello world"]))
            assert message.replies[-1] == "generated result"
            assert router.calls[-1]["local_only"] is True
            assert "Bengali" in router.calls[-1]["messages"][0]["content"]

            await handlers.study(update, SimpleNamespace(args=["quiz", "photosynthesis"]))
            assert router.calls[-1]["local_only"] is True
            assert "valid JSON object" in router.calls[-1]["messages"][0]["content"]
            bot.send_poll.assert_awaited_once_with(
                chat_id=34,
                question="What is photosynthesis?",
                options=["Making food using light", "Breaking down rock"],
                type="quiz",
                correct_option_id=0,
                explanation="Plants convert light into energy.",
                is_anonymous=False,
            )
            usage = await db.usage_today(34)
            assert usage["requests"] == 2
        finally:
            await db.close()

    asyncio.run(scenario())


def test_send_study_quiz_rejects_invalid_telegram_poll_payload() -> None:
    from unittest.mock import AsyncMock

    from enan_ultra.handlers import BotHandlers

    async def scenario() -> None:
        bot = SimpleNamespace(send_poll=AsyncMock())
        handlers = BotHandlers(None, None, None, None, None, None)
        handlers._installing_app = SimpleNamespace(bot=bot)
        invalid_payloads = [
            {"question": "Q", "options": ["A"], "correct_index": 0, "explanation": ""},
            {"question": "Q", "options": ["A", "B"], "correct_index": 2, "explanation": ""},
            {"question": "Q" * 301, "options": ["A", "B"], "correct_index": 0, "explanation": ""},
            {"question": "Q", "options": ["A", "B" * 101], "correct_index": 0, "explanation": ""},
            {"question": "Q", "options": ["A", "B"], "correct_index": True, "explanation": ""},
            {"question": "Q", "options": ["A", "B"], "correct_index": 0, "explanation": "E" * 201},
        ]
        for question_data in invalid_payloads:
            with pytest.raises(ValueError):
                await handlers.send_study_quiz(34, question_data)
        bot.send_poll.assert_not_awaited()

    asyncio.run(scenario())


def test_structured_response_format_adds_explicit_prompt_contract() -> None:
    from enan_ultra.handlers import BotHandlers
    from enan_ultra.models import UserProfile

    settings = SimpleNamespace(
        bot_name="Test Bot", persona_profile="clear", media_style="balanced",
    )
    handlers = BotHandlers(settings, None, None, None, None, None)
    prompt = handlers._system_prompt(
        UserProfile(response_format="json"), [], "",
    )
    assert "only valid JSON" in prompt
    markdown = handlers._system_prompt(UserProfile(response_format="markdown"), [], "")
    assert "readable Markdown" in markdown
    custom = handlers._system_prompt(
        UserProfile(persona="tutor", custom_instructions="Use simple examples"),
        [], "",
    )
    assert "progressive steps" in custom
    assert "Use simple examples" in custom
    assert "bounded by all safety" in custom


def test_personal_timezone_and_temporary_chat_preferences(tmp_path: Path) -> None:
    from enan_ultra.handlers import BotHandlers

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str) -> None:
            self.replies.append(text)

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            handlers = BotHandlers(SimpleNamespace(), db, None, None, None, None)

            async def allowed(*args: object, **kwargs: object) -> bool:
                return True

            handlers._gate = allowed
            user = SimpleNamespace(id=45)
            chat = SimpleNamespace(id=45, type="private")
            message = Message()
            update = SimpleNamespace(effective_user=user, effective_chat=chat, effective_message=message)
            await handlers.timezone(update, SimpleNamespace(args=["Asia/Dhaka"]))
            assert await db.get_preference(45, "timezone", "UTC") == "Asia/Dhaka"
            assert "Asia/Dhaka" in message.replies[-1]
            await handlers.temporary_chat(update, SimpleNamespace(args=["on"]))
            assert await db.get_preference(45, "temporary_chat", "off") == "on"
            await handlers.persona(update, SimpleNamespace(args=["custom", "Use", "short", "examples"]))
            assert await db.get_preference(45, "persona", "default") == "custom"
            assert await db.get_preference(45, "custom_instructions", "") == "Use short examples"
            await handlers.timezone(update, SimpleNamespace(args=["Invalid/Timezone"]))
            assert "Unknown time zone" in message.replies[-1]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_command_help_and_menu_are_in_english() -> None:
    from unittest.mock import AsyncMock

    from enan_ultra.handlers import BotHandlers

    async def scenario() -> None:
        handlers = BotHandlers(SimpleNamespace(), None, None, None, None, None)
        replies: list[str] = []

        async def allowed(*args: object, **kwargs: object) -> bool:
            return True

        async def capture_reply(text: str) -> None:
            replies.append(text)

        handlers._gate = allowed
        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=capture_reply))
        await handlers.help(update, SimpleNamespace())
        assert "Conversation:" in replies[-1]
        assert not any("\u0980" <= char <= "\u09ff" for char in replies[-1])

        set_my_commands = AsyncMock()
        app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=set_my_commands))
        await handlers.register_commands(app)
        commands = set_my_commands.await_args.args[0]
        assert all(not any("\u0980" <= char <= "\u09ff" for char in command.description) for command in commands)
        assert {command.command for command in commands} >= {"help", "clear", "reset"}

    asyncio.run(scenario())


def test_clear_user_conversation_only_removes_private_history_and_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            await db.save_message(123, "user", "private conversation")
            await db.save_message(-200, "user", "shared group conversation")
            await db.save_message(456, "user", "another user's conversation")
            await db.add_memory(123, "private fact")
            await db.add_memory(456, "other fact")

            users = await db.active_users()
            assert {user["user_id"] for user in users} == {123, 456}
            assert next(user for user in users if user["user_id"] == 123)["message_count"] == 1

            await db.clear_user_conversation(123)
            assert await db.history(123, 10) == []
            assert (await db.history(-200, 10))[0]["content"] == "shared group conversation"
            assert (await db.history(456, 10))[0]["content"] == "another user's conversation"
            assert await db.memories(123) == []
            assert len(await db.memories(456)) == 1
            assert {user["user_id"] for user in await db.active_users()} == {456}
        finally:
            await db.close()

    asyncio.run(scenario())


def test_user_and_admin_commands_clear_conversation_memory(tmp_path: Path) -> None:
    from enan_ultra.handlers import BotHandlers

    class Message:
        def __init__(self):
            self.replies: list[str] = []

        async def reply_text(self, text: str):
            self.replies.append(text)

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            await db.save_message(123, "user", "user history")
            await db.add_memory(123, "user memory")
            await db.save_message(456, "user", "admin target history")
            await db.add_memory(456, "admin target memory")
            settings = SimpleNamespace(
                effective_admin_user_ids=frozenset({999}), access_mode="public",
                admin_chat_id=-100, max_output_chars=4000,
            )
            handlers = BotHandlers(settings, db, None, None, None, None)

            user_message = Message()
            user_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=123, full_name="User"),
                effective_chat=SimpleNamespace(id=123, type="private"),
                effective_message=user_message,
            )
            await handlers.reset(user_update, SimpleNamespace(args=[]))
            assert user_message.replies[-1] == (
                "All of your previous chat memories have been successfully deleted! 🗑️"
            )
            assert await db.history(123, 10) == [] and await db.memories(123) == []

            admin_message = Message()
            admin_update = SimpleNamespace(
                effective_user=SimpleNamespace(id=999, full_name="Admin"),
                effective_chat=SimpleNamespace(id=999, type="private"),
                effective_message=admin_message,
            )
            await handlers.clear_user_memory(admin_update, SimpleNamespace(args=["456"]))
            assert "456" in admin_message.replies[-1]
            assert await db.history(456, 10) == [] and await db.memories(456) == []
            assert any(
                item["action"] == "memory.clear" and item["target"] == "456"
                for item in await db.recent_audit_events()
            )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_usage_caps_reserve_requests_and_global_cost(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            event, reason = await db.reserve_usage(1, "model", .6, 1, 1.0)
            assert event is not None and reason is None
            event, reason = await db.reserve_usage(1, "model", .1, 1, 1.0)
            assert event is None and reason == "request_limit"
            event, reason = await db.reserve_usage(2, "model", .5, 1, 1.0)
            assert event is None and reason == "cost_limit"
            event, reason = await db.reserve_usage(
                3, "model", .1, 10, 10.0, user_monthly_request_limit=1,
            )
            assert event is not None and reason is None
            event, reason = await db.reserve_usage(
                3, "model", .1, 10, 10.0, user_monthly_request_limit=1,
            )
            assert event is None and reason == "monthly_request_limit"
            event, reason = await db.reserve_usage(
                4, "model", .5, 10, 10.0, user_monthly_cost_limit=.4,
            )
            assert event is None and reason == "monthly_cost_limit"
            summary = await db.usage_today(3)
            assert summary["monthly_requests"] == 1
        finally:
            await db.close()

    asyncio.run(scenario())


def _zip_with(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _archive_settings() -> SimpleNamespace:
    return SimpleNamespace(max_upload_bytes=2_000_000, sandbox_max_files=20, sandbox_max_expanded_bytes=100_000)


def test_project_zip_rejects_traversal_and_excludes_secrets(tmp_path: Path) -> None:
    traversal = _zip_with({"../outside.py": b"bad"})
    with pytest.raises(ValueError, match="unsafe path"):
        extract_project_zip(traversal, tmp_path / "escape", _archive_settings())

    accepted = _zip_with({".env": b"TOKEN=private", "src/main.py": b"print('safe')"})
    paths = extract_project_zip(accepted, tmp_path / "safe", _archive_settings())
    assert [path.relative_to(tmp_path / "safe").as_posix() for path in paths] == ["src/main.py"]


def test_project_zip_enforces_expanded_size(tmp_path: Path) -> None:
    data = _zip_with({"payload.txt": b"x" * 1000})
    limits = SimpleNamespace(max_upload_bytes=2_000_000, sandbox_max_files=20, sandbox_max_expanded_bytes=100)
    with pytest.raises(ValueError, match="expands"):
        extract_project_zip(data, tmp_path / "bounded", limits)


def test_local_only_file_adapter_never_transcribes_audio() -> None:
    from contextvars import ContextVar

    class FakeRouter:
        calls = 0

        async def transcribe(self, *_args):
            self.calls += 1
            return "must not be called"

    async def scenario() -> None:
        permission = ContextVar("test_remote_media", default=True)
        router = FakeRouter()
        adapter = AudioAdapter(router, permission)
        permission.set(False)
        result, _ = await adapter.extract_with_media(
            "disguised.bin", "application/octet-stream", b"OggS" + b"x" * 100,
            SimpleNamespace(voice_input_enabled=True),
        )
        assert router.calls == 0
        assert result[0].modality == "warning"

    asyncio.run(scenario())


def test_image_adapter_rejects_excessive_pixel_dimensions() -> None:
    from PIL import Image

    image_data = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(image_data, format="PNG")

    async def scenario() -> None:
        adapter = ImageAdapter()
        chunks, images = await adapter.extract_with_media(
            "bomb.png", "image/png", image_data.getvalue(),
            SimpleNamespace(max_image_pixels=100, max_image_bytes=1_000_000),
        )
        assert not images
        assert chunks and chunks[0].modality == "warning"
        assert "pixel limit" in chunks[0].text

    asyncio.run(scenario())


class _FakeAdapter:
    def __init__(self, name: str, model: str, *, fail: bool = False) -> None:
        self.name, self.model, self.fail = name, model, fail
        self.calls = 0

    def configured_model(self) -> str:
        return self.model

    def capabilities(self):
        return {"input": ["text", "image"], "output": ["text"], "tools": True}

    async def complete(self, model, messages, *, tools, max_tokens, on_token=None):
        self.calls += 1
        if self.fail:
            raise ProviderError("simulated provider outage")
        return AssistantResult("routed", f"{self.name}::{model}")

    async def list_models(self):
        return [self.model]

    async def health(self):
        return {"status": "healthy"}

    async def close(self):
        return None


def test_provider_fallback_and_local_only_never_call_cloud() -> None:
    async def scenario() -> None:
        router = object.__new__(ProviderRouter)
        router.settings = SimpleNamespace(
            provider_order=("openrouter", "groq", "gemini", "cloudflare", "huggingface"),
            fallback_models=(),
        )
        router.openrouter = None
        cloud = _FakeAdapter("openrouter", "cloud-model", fail=True)
        groq = _FakeAdapter("groq", "groq-model", fail=True)
        gemini = _FakeAdapter("gemini", "gemini-model", fail=True)
        cloudflare = _FakeAdapter("cloudflare", "cloudflare-model", fail=True)
        huggingface = _FakeAdapter("huggingface", "hf-model")
        local = _FakeAdapter("ollama", "local-model")
        router.adapters = {
            "openrouter": cloud, "groq": groq, "gemini": gemini,
            "cloudflare": cloudflare, "huggingface": huggingface, "ollama": local,
        }
        result = await router.complete([], primary_model="openrouter::cloud-model", local_only=False)
        assert result.model == "huggingface::hf-model"
        assert cloud.calls == groq.calls == gemini.calls == cloudflare.calls == huggingface.calls == 1
        assert local.calls == 0

        cloud.calls = groq.calls = gemini.calls = cloudflare.calls = huggingface.calls = local.calls = 0
        result = await router.complete([], primary_model="openrouter::cloud-model", local_only=True)
        assert result.model == "ollama::local-model"
        assert cloud.calls == groq.calls == gemini.calls == cloudflare.calls == huggingface.calls == 0
        assert local.calls == 1

    asyncio.run(scenario())


def test_key_rotation_retries_only_eligible_statuses_and_rotates_starting_key(caplog) -> None:
    async def scenario() -> None:
        rotation = KeyRotation("test-provider", ("key-a", "key-b", "key-c"))
        attempted: list[str] = []

        async def fail_then_succeed(key: str | None, _index: int) -> str:
            attempted.append(key or "")
            if key == "key-a":
                raise ProviderError("rate limited", 429)
            return key or ""

        assert await rotation.run(fail_then_succeed) == "key-b"
        assert attempted == ["key-a", "key-b"]
        assert "test-provider API key 1 failed with HTTP 429" in caplog.text

        attempted.clear()

        async def next_request(key: str | None, _index: int) -> str:
            attempted.append(key or "")
            return key or ""

        assert await rotation.run(next_request) == "key-b"
        assert attempted == ["key-b"]

        async def unauthorized(key: str | None, _index: int) -> str:
            attempted.append(key or "")
            raise ProviderError("unauthorized", 401)

        with pytest.raises(ProviderError, match="unauthorized"):
            await rotation.run(unauthorized)
        assert attempted[-3:] == ["key-c", "key-a", "key-b"]

        async def forbidden(_key: str | None, _index: int) -> str:
            raise ProviderError("forbidden", 403)

        with pytest.raises(ProviderError, match="forbidden"):
            await rotation.run(forbidden)

    asyncio.run(scenario())


def test_openai_adapter_retries_request_with_next_authorization_key() -> None:
    from enan_ultra.services.providers import OpenAICompatibleAdapter

    async def scenario() -> None:
        settings = SimpleNamespace(openai_keys=("key-a", "key-b"))
        adapter = OpenAICompatibleAdapter(settings, "openai", "https://api.example.test/v1", "", "test-model")
        await adapter.client.aclose()
        seen_keys: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            seen_keys.append(request.headers.get("authorization", ""))
            if len(seen_keys) == 1:
                return httpx.Response(429, request=request)
            return httpx.Response(
                200,
                json={"model": "test-model", "choices": [{"message": {"content": "ok"}}]},
                request=request,
            )

        adapter.client = httpx.AsyncClient(
            base_url="https://api.example.test/v1", transport=httpx.MockTransport(respond),
        )
        try:
            result = await adapter.complete(
                "test-model", [{"role": "user", "content": "hello"}],
                tools=None, max_tokens=20,
            )
            assert result.text == "ok"
            assert seen_keys == [f"Bearer {key}" for key in settings.openai_keys]
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("provider", "credential_header"),
    [("openrouter", "authorization"), ("anthropic", "x-api-key"), ("gemini", "x-goog-api-key")],
)
def test_provider_adapters_retry_auth_or_quota_failures_with_next_key(
    provider: str, credential_header: str,
) -> None:
    from enan_ultra.services.openrouter import OpenRouter
    from enan_ultra.services.providers import AnthropicAdapter, GeminiAdapter

    async def scenario() -> None:
        settings = SimpleNamespace(
            openrouter_keys=("key-a", "key-b"),
            openrouter_base_url="https://api.example.test/v1",
            display_name="test",
            anthropic_keys=("key-a", "key-b"),
            anthropic_base_url="https://api.example.test",
            gemini_keys=("key-a", "key-b"),
            gemini_base_url="https://api.example.test/v1beta",
            anthropic_model="test-model",
            gemini_model="test-model",
        )
        if provider == "openrouter":
            adapter = OpenRouter(settings)
        elif provider == "anthropic":
            adapter = AnthropicAdapter(settings)
        else:
            adapter = GeminiAdapter(settings)

        await adapter.client.aclose()
        seen_keys: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            seen_keys.append(request.headers.get(credential_header, ""))
            if len(seen_keys) == 1:
                return httpx.Response(402, request=request)
            return httpx.Response(200, json={"data": []}, request=request)

        adapter.client = httpx.AsyncClient(
            base_url="https://api.example.test", transport=httpx.MockTransport(respond),
        )
        try:
            response = await adapter._request("GET", "/probe")
            assert response.status_code == 200
            assert [value.removeprefix("Bearer ") for value in seen_keys] == ["key-a", "key-b"]
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("groq", "https://api.groq.com/openai/v1"),
        ("together", "https://api.together.xyz/v1"),
        ("sambanova", "https://api.sambanova.ai/v1"),
        (
            "cloudflare",
            "https://api.cloudflare.com/client/v4/accounts/"
            "0123456789abcdef0123456789abcdef/ai/v1",
        ),
    ],
)
def test_openai_compatible_provider_adapters_rotate_credentials(
    provider: str, base_url: str,
) -> None:
    from enan_ultra.services.providers import OpenAICompatibleAdapter

    async def scenario() -> None:
        settings = SimpleNamespace(**{f"{provider}_keys": ("key-a", "key-b")})
        adapter = OpenAICompatibleAdapter(settings, provider, base_url, "", "test-model")
        await adapter.client.aclose()
        seen_keys: list[str] = []

        async def respond(request: httpx.Request) -> httpx.Response:
            seen_keys.append(request.headers.get("authorization", ""))
            if len(seen_keys) == 1:
                return httpx.Response(429, request=request)
            return httpx.Response(200, json={"data": []}, request=request)

        adapter.client = httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(respond),
        )
        try:
            response = await adapter._request("GET", "/models")
            assert response.status_code == 200
            assert seen_keys == ["Bearer key-a", "Bearer key-b"]
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_settings_loads_supported_provider_and_rejects_public_dashboard(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    required = {
        "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": "123456",
        "GEMINI_API_KEY": "test-only-provider-key",
        "GEMINI_MODEL": "test-model",
        "APP_DATA_KEY": KEY,
        "DASHBOARD_AUTH_TOKEN": "test-only-dashboard-auth-token-1234567890abcdef",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    settings = Settings.load()
    assert settings.persona_profile == "GPT-6 Astra"
    assert settings.dashboard_host == "127.0.0.1"
    assert settings.gemini_keys == ("test-only-provider-key",)

    monkeypatch.setenv("GEMINI_API_KEYS", "first-test-key, second-test-key")
    settings = Settings.load()
    assert settings.gemini_keys == ("first-test-key", "second-test-key")

    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="127.0.0.1"):
        Settings.load()


def test_settings_loads_free_provider_keys_and_cloudflare_endpoint(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    required = {
        "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": "123456",
        "APP_DATA_KEY": KEY,
        "DASHBOARD_AUTH_TOKEN": "test-only-dashboard-auth-token-1234567890abcdef",
        "OPENROUTER_API_KEYS": "router-one, router-two",
        "GEMINI_API_KEY": "gemini-one",
        "GEMINI_MODEL": "gemini-test",
        "GROQ_API_KEYS": "groq-one, groq-two",
        "GROQ_MODEL": "groq-test",
        "CLOUDFLARE_API_KEYS": "cloudflare-one, cloudflare-two",
        "CLOUDFLARE_ACCOUNT_ID": "0123456789abcdef0123456789abcdef",
        "CLOUDFLARE_MODEL": "@cf/test-model",
        "HUGGINGFACE_API_KEYS": "hf-one, hf-two",
        "HUGGINGFACE_MODEL": "meta-llama/test-instruct",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    settings = Settings.load()
    assert settings.openrouter_keys == ("router-one", "router-two")
    assert settings.gemini_keys == ("gemini-one",)
    assert settings.groq_keys == ("groq-one", "groq-two")
    assert settings.cloudflare_keys == ("cloudflare-one", "cloudflare-two")
    assert settings.huggingface_keys == ("hf-one", "hf-two")
    assert settings.cloudflare_base_url.endswith(
        "/accounts/0123456789abcdef0123456789abcdef/ai/v1",
    )
    assert settings.provider_order == (
        "openrouter", "groq", "gemini", "cloudflare", "huggingface",
    )
    assert settings.huggingface_base_url == "https://router.huggingface.co/v1"
    router = ProviderRouter(settings, None)
    assert {"groq", "gemini", "cloudflare", "huggingface"} == set(router.adapters)

    async def verify_huggingface_key_rotation() -> None:
        adapter = router.adapters["huggingface"]
        assert isinstance(adapter, OpenAICompatibleAdapter)
        requests: list[tuple[str, str]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append((request.headers["Authorization"], request.url.path))
            if len(requests) == 1:
                return httpx.Response(429, request=request)
            return httpx.Response(
                200,
                json={"model": "meta-llama/test-instruct", "choices": [
                    {"message": {"content": "Hugging Face response"}},
                ]},
                request=request,
            )

        await adapter.close()
        adapter.client = httpx.AsyncClient(
            base_url=settings.huggingface_base_url,
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await adapter.complete(
                "meta-llama/test-instruct", [{"role": "user", "content": "hello"}],
                tools=None, max_tokens=64,
            )
            assert response.text == "Hugging Face response"
            assert response.model == "huggingface::meta-llama/test-instruct"
            assert requests == [
                ("Bearer hf-one", "/v1/chat/completions"),
                ("Bearer hf-two", "/v1/chat/completions"),
            ]
        finally:
            await router.close()

    asyncio.run(verify_huggingface_key_rotation())


def test_deployment_env_generates_and_preserves_secrets(tmp_path: Path, monkeypatch, capsys) -> None:
    import runpy

    prepare_deploy_env = SimpleNamespace(**runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "prepare_deploy_env.py"),
    ))

    path = tmp_path / "bot.env"
    path.write_text(
        "TELEGRAM_BOT_TOKEN=replace-with-telegram-bot-token\nADMIN_CHAT_ID=123\nADMIN_USER_IDS=123\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["prepare_deploy_env.py", str(path)])
    assert prepare_deploy_env.main() == 2
    first = path.read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in first.splitlines() if "=" in line and not line.startswith("#"))
    assert len(values["APP_DATA_KEY"]) >= 32 and "replace-" not in values["APP_DATA_KEY"]
    assert len(values["DASHBOARD_AUTH_TOKEN"]) >= 32
    path.write_text(first.replace("TELEGRAM_BOT_TOKEN=replace-with-telegram-bot-token", "TELEGRAM_BOT_TOKEN=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
                   .replace("OPENROUTER_API_KEYS=", "OPENROUTER_API_KEYS=test-only-key"), encoding="utf-8")
    assert prepare_deploy_env.main() == 0
    second = path.read_text(encoding="utf-8")
    assert f"APP_DATA_KEY={values['APP_DATA_KEY']}" in second
    assert f"DASHBOARD_AUTH_TOKEN={values['DASHBOARD_AUTH_TOKEN']}" in second


def test_dashboard_requires_login_and_same_origin() -> None:
    class FakeDb:
        def __init__(self):
            self.cleared_users: list[int] = []
            self.audit_events: list[tuple] = []

        async def active_live_count(self):
            return 0

        async def active_users(self):
            return [{"user_id": 123, "last_active": "2026-10-15T01:00:00+00:00", "message_count": 2}]

        async def clear_user_conversation(self, user_id: int):
            self.cleared_users.append(user_id)
            return {"messages_deleted": 2, "memories_deleted": 1}

        async def audit(self, *event):
            self.audit_events.append(event)

    settings = SimpleNamespace(
        dashboard_host="127.0.0.1", dashboard_port=8787,
        dashboard_auth_token="dashboard-test-secret-with-more-than-32-characters",
        effective_admin_user_ids=frozenset({123}),
        database_path=Path(__file__).resolve(),
    )
    db = FakeDb()
    dashboard = AdminDashboard(settings, db, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=dashboard.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            assert (await client.get("/api/v1/status")).status_code == 401
            login = await client.post(
                "/auth/login", json={"token": settings.dashboard_auth_token},
                headers={"Origin": "http://127.0.0.1"},
            )
            assert login.status_code == 200
            assert (await client.get("/api/v1/status")).json()["dashboard"] == "loopback"
            assert (await client.get("/api/v1/users")).json()[0]["user_id"] == 123
            assert (await client.post("/api/v1/users/123/clear-memory", json={})).status_code == 403
            cleared = await client.post(
                "/api/v1/users/123/clear-memory", json={},
                headers={"Origin": "http://127.0.0.1"},
            )
            assert cleared.status_code == 200
            assert cleared.json() == {"ok": True}
            assert db.cleared_users == [123]
            assert db.audit_events[0][1:3] == ("memory.clear", "123")
            assert (
                await client.post(
                    "/api/v1/users/-123/clear-memory", json={},
                    headers={"Origin": "http://127.0.0.1"},
                )
            ).status_code == 400
            health = (await client.get("/api/v1/health")).json()
            assert health["status"] in {"ok", "degraded"}
            assert health["resources"]["disk_total_bytes"] > 0
            assert (await client.post("/api/v1/approvals/1/approve", json={})).status_code == 403
            assert (await client.post("/auth/logout", headers={"Origin": "http://127.0.0.1"})).status_code == 200

    asyncio.run(scenario())


def test_resource_metrics_report_disk_and_valid_optional_host_values(tmp_path: Path) -> None:
    from enan_ultra.services.health import health_snapshot, resource_metrics

    metrics = resource_metrics(tmp_path / "assistant.sqlite3")
    assert metrics["disk_total_bytes"] > 0
    assert 0 <= metrics["disk_used_percent"] <= 100
    assert metrics["disk_free_bytes"] >= 0
    snapshot = health_snapshot(tmp_path / "assistant.sqlite3")
    assert snapshot["status"] in {"ok", "degraded"}
    if metrics["memory_used_percent"] is not None:
        assert 0 <= metrics["memory_used_percent"] <= 100


def test_resource_monitor_alerts_with_six_hour_rate_limit(tmp_path: Path, monkeypatch) -> None:
    from enan_ultra import runtime

    async def scenario() -> None:
        db = Database(tmp_path / "assistant.sqlite3", data_key=KEY)
        await db.open()
        try:
            sent: list[str] = []

            class Bot:
                async def send_message(self, _chat_id: int, text: str):
                    sent.append(text)

            monkeypatch.setattr(
                runtime, "resource_metrics",
                lambda _path: {
                    "disk_free_bytes": 10, "memory_used_percent": 97.0,
                },
            )
            app = SimpleNamespace(bot=Bot())
            settings = SimpleNamespace(admin_alerts=True, admin_chat_id=12)
            await runtime._monitor_resources(app, db, settings)
            await runtime._monitor_resources(app, db, settings)
            assert len(sent) == 1
            assert "resource alert" in sent[0]
        finally:
            await db.close()

    asyncio.run(scenario())
