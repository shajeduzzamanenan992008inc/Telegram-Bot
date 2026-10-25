from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import TelegramError

from enan_ultra.crypto import DataCipher
from enan_ultra.handlers import BotHandlers
from enan_ultra.models import AssistantResult
from enan_ultra.services.code_sandbox import (
    redact_sandbox_output, sanitize_project_archive, validate_complete_project_archive,
    validate_evolution_patch,
)
from enan_ultra.services.mission_control import MissionControl
from enan_ultra.storage.database import Database

KEY = "test-only-app-data-key-with-sufficient-entropy-123456789"


def _settings(**overrides):
    values = {
        "agent_max_workers": 2,
        "agent_max_steps": 2,
        "agent_max_cost_usd": 1.0,
        "agent_enabled_by_default": True,
        "user_daily_request_limit": 40,
        "max_daily_cost_usd": 5.0,
        "user_monthly_request_limit": 1200,
        "user_monthly_cost_limit_usd": 50.0,
        "ai_request_timeout": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _MissionRouter:
    def __init__(self, *, cost: float = 0.0) -> None:
        self.cost = cost
        self.calls: list[list[dict[str, str]]] = []

    def default_model(self) -> str:
        return "local::test-model"

    async def estimate_cost(self, *_args, **_kwargs) -> float:
        return self.cost

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        if "valid JSON only" in messages[0]["content"]:
            return AssistantResult('{"steps":["Find the main question","Summarize the result"]}', "local::test-model")
        return AssistantResult(f"Analysis {len(self.calls)}", "local::test-model")


class _ParallelMissionRouter(_MissionRouter):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.both_started = asyncio.Event()

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        if "valid JSON only" in messages[0]["content"]:
            return AssistantResult(
                '{"steps":[{"title":"Independent A","depends_on":[]},'
                '{"title":"Independent B","depends_on":[]}]}',
                "local::test-model",
            )
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= 2:
            self.both_started.set()
        try:
            await asyncio.wait_for(self.both_started.wait(), timeout=2)
        finally:
            self.active -= 1
        return AssistantResult(f"Parallel analysis {self.active}", "local::test-model")


def _project_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("test_example.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    return output.getvalue()


def _candidate_zip() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("test_example.py", "def test_ok():\n    assert 2 + 2 == 4\n")
    return output.getvalue()


def _patch() -> bytes:
    return (
        b"diff --git a/sample.py b/sample.py\n"
        b"--- a/sample.py\n+++ b/sample.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    )


def test_missions_encrypt_scope_dedupe_recovery_and_delete(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "mission.sqlite3", data_key=KEY)
        await db.open()
        try:
            secret_task = "private launch strategy Orchid"
            payload = {"task": secret_task, "steps": [], "results": []}
            mission_id, created = await db.create_mission(101, 101, secret_task, payload)
            assert created
            duplicate_id, duplicate_created = await db.create_mission(101, 101, secret_task, payload)
            assert duplicate_id == mission_id and not duplicate_created
            assert await db.get_mission(mission_id, 202) is None
            row = (await db._db().execute_fetchall(
                "SELECT payload FROM missions WHERE id=?", (mission_id,),
            ))[0]
            assert secret_task.encode() not in bytes(row[0])
            assert DataCipher(KEY).decrypt_text(row[0])
            assert await db.recover_missions_after_restart() == 1
            paused = await db.get_mission(mission_id, 101)
            assert paused and paused["state"] == "paused"
            assert await db.delete_mission(mission_id, 202) is False
            assert await db.delete_mission(mission_id, 101) is True
        finally:
            await db.close()

    asyncio.run(scenario())


def test_mission_control_persists_sequential_read_only_analysis(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "mission.sqlite3", data_key=KEY)
        await db.open()
        try:
            router = _MissionRouter()
            control = MissionControl(_settings(), db, router)
            progress: list[str] = []

            async def notify(text: str) -> None:
                progress.append(text)

            mission_id, created = await control.create(
                101, 101, "Amar API fallback check koro", local_only=True,
                primary_model="local::test-model", notify=notify,
            )
            assert created
            task = control._tasks[mission_id]
            await asyncio.wait_for(task, timeout=3)
            record = await db.get_mission(mission_id, 101)
            assert record and record["state"] == "completed"
            assert record["completed_steps"] == 2
            assert len(record["payload"]["results"]) == 2
            assert len(router.calls) == 3  # one planner and two analysis steps
            assert all("tools" not in call[0] for call in router.calls)
            assert any("Mission #" in line for line in progress)
            assert await db.get_mission(mission_id, 102) is None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_mission_budget_blocks_before_provider_call(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "mission.sqlite3", data_key=KEY)
        await db.open()
        try:
            router = _MissionRouter(cost=0.2)
            control = MissionControl(_settings(agent_max_cost_usd=0.5, agent_max_steps=2), db, router)
            mission_id, _ = await control.create(
                101, 101, "Review this", local_only=False, primary_model="cloud::model",
                notify=lambda _text: asyncio.sleep(0),
            )
            await asyncio.wait_for(control._tasks[mission_id], timeout=3)
            record = await db.get_mission(mission_id, 101)
            assert record and record["state"] == "failed"
            assert record["error_code"] == "mission_cost_limit"
            assert router.calls == []
        finally:
            await db.close()

    asyncio.run(scenario())


def test_independent_mission_steps_run_concurrently_and_checkpoint_both(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "mission.sqlite3", data_key=KEY)
        await db.open()
        try:
            router = _ParallelMissionRouter()
            control = MissionControl(_settings(agent_max_workers=2), db, router)
            mission_id, _ = await control.create(
                101, 101, "Summarize two independent questions", local_only=True,
                primary_model="local::test-model", notify=lambda _text: asyncio.sleep(0),
            )
            await asyncio.wait_for(control._tasks[mission_id], timeout=8)
            record = await db.get_mission(mission_id, 101)
            assert record and record["state"] == "completed"
            assert record["completed_steps"] == 2
            assert router.max_active == 2
            assert all(
                isinstance(item, dict) and item["text"].startswith("Parallel analysis")
                for item in record["payload"]["results"]
            )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_evolution_hash_gates_admin_approval_and_separate_deploy_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "evolution.sqlite3", data_key=KEY)
        await db.open()
        try:
            patch, archive, candidate = _patch(), _project_zip(), _candidate_zip()
            proposal_id = await db.create_evolution_proposal(101, "Fix a test", patch, archive)
            proposal = await db.get_evolution_proposal(proposal_id)
            assert proposal and proposal["patch_hash"] == hashlib.sha256(patch).hexdigest()
            assert proposal["source_hash"] == hashlib.sha256(archive).hexdigest()
            raw = (await db._db().execute_fetchall(
                "SELECT summary,payload FROM evolution_proposals WHERE id=?", (proposal_id,),
            ))[0]
            assert "Fix a test" not in str(raw[0][0])
            encrypted_payload = str(raw[0][1]).encode("utf-8")
            assert patch not in encrypted_payload and archive not in encrypted_payload
            assert not await db.record_evolution_test(proposal_id, "0" * 64, "passed", 0, False)
            assert await db.record_evolution_test(proposal_id, proposal["patch_hash"], "2 failed", 1, False)
            assert await db.decide_evolution_staging(
                proposal_id, proposal["patch_hash"], 10, frozenset({10}), approve=True,
            ) is False

            proposal_id = await db.create_evolution_proposal(101, "Fix a test", patch, archive)
            proposal = await db.get_evolution_proposal(proposal_id)
            assert proposal
            patch_hash = proposal["patch_hash"]
            assert await db.record_evolution_test(proposal_id, patch_hash, "2 passed", 0, False)
            assert await db.decide_evolution_staging(
                proposal_id, "f" * 64, 10, frozenset({10}), approve=True,
            ) is False
            assert await db.decide_evolution_staging(
                proposal_id, patch_hash, 99, frozenset({10}), approve=True,
            ) is False
            assert await db.decide_evolution_staging(
                proposal_id, patch_hash, 10, frozenset({10}), approve=True,
            ) is True
            decisions = await asyncio.gather(*(
                db.decide_evolution_staging(
                    proposal_id, patch_hash, 10, frozenset({10}), approve=True,
                ) for _ in range(2)
            ))
            assert decisions == [False, False]
            assert await db.record_evolution_staging_verification(
                proposal_id, patch_hash, 99, frozenset({10}), "passed", 0, False,
            ) is False
            assert await db.record_evolution_staging_verification(
                proposal_id, patch_hash, 10, frozenset({10}), "passed", 0, False,
            ) is False
            assert await db.record_evolution_staging_verification(
                proposal_id, patch_hash, 10, frozenset({10}), "passed", 0, False,
                b"x" * (20 * 1024 * 1024 + 1),
            ) is False
            assert await db.record_evolution_staging_verification(
                proposal_id, patch_hash, 10, frozenset({10}), "passed", 0, False,
                candidate,
            ) is True
            staged = await db.get_evolution_proposal(proposal_id)
            assert staged
            assert base64.b64decode(staged["payload"]["candidate_archive"]) == candidate
            assert staged["payload"]["candidate_hash"] == hashlib.sha256(candidate).hexdigest()
            assert staged["payload"]["candidate_hash"] != proposal["source_hash"]
            assert await db.approve_evolution_deployment(
                proposal_id, "f" * 64, 10, frozenset({10}),
            ) is False
            assert await db.approve_evolution_deployment(
                proposal_id, patch_hash, 10, frozenset({10}),
            ) is True
            assert not await db.approve_evolution_deployment(
                proposal_id, patch_hash, 10, frozenset({10}),
            )

            expired_id = await db.create_evolution_proposal(101, "Expire this", patch, archive)
            expired = await db.get_evolution_proposal(expired_id)
            assert expired
            await db._db().execute(
                "UPDATE evolution_proposals SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (expired_id,),
            )
            await db._db().commit()
            assert await db.expire_evolution_proposals() == 1
            expired = await db.get_evolution_proposal(expired_id)
            assert expired and expired["state"] == "expired" and expired["payload"] == {}
            assert not await db.decide_evolution_staging(
                expired_id, expired["patch_hash"], 10, frozenset({10}), approve=True,
            )
        finally:
            await db.close()

    asyncio.run(scenario())


def test_patch_path_policy_and_sandbox_log_redaction() -> None:
    safe = _patch()
    assert "sample.py" in validate_evolution_patch(safe)
    protected = (
        b"--- a/src/enan_ultra/security.py\n+++ b/src/enan_ultra/security.py\n"
        b"@@ -1 +1 @@\n-safe = True\n+safe = False\n"
    )
    with pytest.raises(ValueError, match="protected"):
        validate_evolution_patch(protected)
    dependency = b"--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1 @@\n-pytest\n+unknown-package\n"
    with pytest.raises(ValueError, match="protected"):
        validate_evolution_patch(dependency)
    traversal = b"--- a/../../etc/passwd\n+++ b/../../etc/passwd\n"
    with pytest.raises(ValueError, match="unsafe"):
        validate_evolution_patch(traversal)
    secret_file = b"--- /dev/null\n+++ b/.env.production\n@@ -0,0 +1 @@\n+TOKEN=value\n"
    with pytest.raises(ValueError, match="protected"):
        validate_evolution_patch(secret_file)
    credential = (
        b"--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n- api_key = ''\n+ api_key = 'private-credential-value-12345'\n"
    )
    with pytest.raises(ValueError, match="credential"):
        validate_evolution_patch(credential)
    output = redact_sandbox_output(
        "Authorization: Bearer abcdef api_key=secret123 123456:abcdefghijklmnopqrstuvwxyz"
    )
    assert "abcdef" not in output and "secret123" not in output and "abcdefghijklmnopqrstuvwxyz" not in output


def test_evolution_source_repack_excludes_secret_files() -> None:
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("src/app.py", "VALUE = 1\n")
        archive.writestr(".env", "OPENAI_API_KEY=private-key\n")
    settings = SimpleNamespace(
        max_upload_bytes=1_000_000,
        sandbox_max_files=20,
        sandbox_max_expanded_bytes=1_000_000,
    )
    clean = sanitize_project_archive(source.getvalue(), settings)
    with zipfile.ZipFile(io.BytesIO(clean)) as archive:
        assert archive.namelist() == ["src/app.py"]
        assert b"private-key" not in archive.read("src/app.py")


def test_complete_evolution_archive_validation_rejects_silently_excluded_files() -> None:
    settings = SimpleNamespace(
        max_upload_bytes=1_000_000,
        sandbox_max_files=20,
        sandbox_max_expanded_bytes=1_000_000,
    )
    validate_complete_project_archive(_project_zip(), settings)

    contaminated = io.BytesIO()
    with zipfile.ZipFile(contaminated, "w") as archive:
        archive.writestr("src/app.py", "VALUE = 1\n")
        archive.writestr(".env", "TOKEN=secret\n")
    with pytest.raises(ValueError, match="excluded"):
        validate_complete_project_archive(contaminated.getvalue(), settings)


def test_evolution_export_requires_admin_private_chat_and_exact_hash() -> None:
    async def scenario() -> None:
        candidate, source = _candidate_zip(), _project_zip()
        patch_hash = hashlib.sha256(_patch()).hexdigest()
        candidate_hash = hashlib.sha256(candidate).hexdigest()
        proposal = {
            "patch_hash": patch_hash,
            "source_hash": hashlib.sha256(source).hexdigest(),
            "state": "awaiting_deployment_approval",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "payload": {
                "candidate_archive": base64.b64encode(candidate).decode("ascii"),
                "candidate_hash": candidate_hash,
                "archive": base64.b64encode(source).decode("ascii"),
            },
        }
        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            max_upload_bytes=1_000_000,
            sandbox_max_files=20,
            sandbox_max_expanded_bytes=1_000_000,
        )
        handlers._gate = AsyncMock(return_value=True)
        handlers.db = SimpleNamespace(
            get_evolution_proposal=AsyncMock(return_value=proposal),
            audit=AsyncMock(),
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_document=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=10),
        )

        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
        )
        message.reply_document.assert_awaited_once()
        sent = message.reply_document.await_args.args[0]
        assert sent.filename == "evolution-7-candidate.zip"
        assert sent.input_file_content == candidate
        assert candidate_hash in message.reply_document.await_args.kwargs["caption"]
        handlers.db.audit.assert_awaited_once_with(
            10, "evolution.artifact_export", "7",
            f"candidate:success:{candidate_hash[:16]}",
        )

        handlers.db.audit.reset_mock()
        message.reply_document.reset_mock(side_effect=True)
        message.reply_document.side_effect = TelegramError("delivery failed")
        message.reply_text.reset_mock()
        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
        )
        message.reply_document.assert_awaited_once()
        handlers.db.audit.assert_awaited_once_with(
            10, "evolution.artifact_export", "7", "candidate:failed:delivery",
        )
        assert "could not be delivered" in message.reply_text.await_args.args[0]
        message.reply_document.side_effect = None

        message.reply_text.reset_mock()
        message.reply_document.reset_mock(side_effect=True)
        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", "0" * 64, "candidate"]),
        )
        message.reply_document.assert_not_awaited()
        assert "unavailable" in message.reply_text.await_args.args[0]

        proposal["expires_at"] = "2000-01-01T00:00:00+00:00"
        message.reply_text.reset_mock()
        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
        )
        message.reply_document.assert_not_awaited()
        assert "current" in message.reply_text.await_args.args[0]
        assert handlers.db.audit.await_args.args[-1] == "candidate:blocked:proposal_unavailable"

        handlers._gate = AsyncMock(return_value=False)
        message.reply_text.reset_mock()
        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
        )
        assert handlers.db.get_evolution_proposal.await_count == 4
        message.reply_document.assert_not_awaited()

        handlers._gate = AsyncMock(return_value=True)
        update.effective_chat.type = "group"
        message.reply_text.reset_mock()
        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
        )
        assert handlers.db.get_evolution_proposal.await_count == 4
        assert "private chat" in message.reply_text.await_args.args[0]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("candidate_kind", "expected_outcome"),
    [
        ("hash_mismatch", "candidate:blocked:hash_mismatch"),
        ("unsafe_archive", "candidate:blocked:archive_validation"),
        ("oversized_archive", "candidate:blocked:archive_validation"),
    ],
)
def test_evolution_export_rejects_tampered_unsafe_and_oversized_candidates(
    candidate_kind: str, expected_outcome: str,
) -> None:
    async def scenario() -> None:
        candidate = _candidate_zip()
        max_upload_bytes = 1_000_000
        if candidate_kind == "hash_mismatch":
            candidate_hash = "0" * 64
        elif candidate_kind == "unsafe_archive":
            unsafe = io.BytesIO()
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr(".env", "TOKEN=secret\n")
            candidate = unsafe.getvalue()
            candidate_hash = hashlib.sha256(candidate).hexdigest()
        else:
            oversized = io.BytesIO()
            with zipfile.ZipFile(oversized, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("large.py", b"x" * 256)
            candidate = oversized.getvalue()
            candidate_hash = hashlib.sha256(candidate).hexdigest()
            max_upload_bytes = 128

        patch_hash = hashlib.sha256(_patch()).hexdigest()
        proposal = {
            "patch_hash": patch_hash,
            "source_hash": hashlib.sha256(_project_zip()).hexdigest(),
            "state": "awaiting_deployment_approval",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "payload": {
                "candidate_archive": base64.b64encode(candidate).decode("ascii"),
                "candidate_hash": candidate_hash,
            },
        }
        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            max_upload_bytes=max_upload_bytes,
            sandbox_max_files=20,
            sandbox_max_expanded_bytes=1_000_000,
        )
        handlers._gate = AsyncMock(return_value=True)
        handlers.db = SimpleNamespace(
            get_evolution_proposal=AsyncMock(return_value=proposal),
            audit=AsyncMock(),
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_document=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=10),
        )

        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
        )

        message.reply_document.assert_not_awaited()
        assert handlers.db.audit.await_args.args[-1] == expected_outcome

    asyncio.run(scenario())


def test_evolution_export_audits_interrupted_delivery_and_propagates_cancellation() -> None:
    async def scenario() -> None:
        candidate = _candidate_zip()
        patch_hash = hashlib.sha256(_patch()).hexdigest()
        candidate_hash = hashlib.sha256(candidate).hexdigest()
        proposal = {
            "patch_hash": patch_hash,
            "state": "deployment_approved",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "payload": {
                "candidate_archive": base64.b64encode(candidate).decode("ascii"),
                "candidate_hash": candidate_hash,
            },
        }
        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            max_upload_bytes=1_000_000,
            sandbox_max_files=20,
            sandbox_max_expanded_bytes=1_000_000,
        )
        handlers._gate = AsyncMock(return_value=True)
        handlers.db = SimpleNamespace(
            get_evolution_proposal=AsyncMock(return_value=proposal),
            audit=AsyncMock(),
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_document=AsyncMock(side_effect=asyncio.CancelledError),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=10),
        )

        with pytest.raises(asyncio.CancelledError):
            await handlers.evolve_export(
                update, SimpleNamespace(args=["7", patch_hash, "candidate"]),
            )

        handlers.db.audit.assert_awaited_once_with(
            10, "evolution.artifact_export", "7", "candidate:failed:delivery_interrupted",
        )

    asyncio.run(scenario())


def test_evolution_export_uses_real_admin_gate_before_loading_proposal() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            effective_admin_user_ids=frozenset({10}),
            access_mode="public",
            allowed_user_ids=frozenset(),
            admin_chat_id=100,
        )
        handlers.db = SimpleNamespace(
            get_evolution_proposal=AsyncMock(),
            audit=AsyncMock(),
            is_user_blocked=AsyncMock(return_value=False),
        )
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_document=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private", id=20),
            effective_user=SimpleNamespace(id=99),
        )

        await handlers.evolve_export(
            update, SimpleNamespace(args=["7", "0" * 64, "candidate"]),
        )

        handlers.db.get_evolution_proposal.assert_not_awaited()
        handlers.db.audit.assert_not_awaited()
        handlers.db.is_user_blocked.assert_not_awaited()
        message.reply_document.assert_not_awaited()
        assert "authorized administrators" in message.reply_text.await_args.args[0]

    asyncio.run(scenario())


def test_private_gate_rejects_group_chat_before_any_action() -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace()
        handlers.db = SimpleNamespace(is_user_blocked=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=10),
            effective_message=message,
            effective_chat=SimpleNamespace(type="group"),
        )

        assert await handlers._gate(update, private=True) is False
        handlers.db.is_user_blocked.assert_not_awaited()
        message.reply_text.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected_stored"),
    [("off", False), ("on", True), (None, True)],
)
def test_evolution_mode_gates_proposal_storage(mode: str | None, expected_stored: bool) -> None:
    async def scenario() -> None:
        handlers = object.__new__(BotHandlers)
        handlers.settings = SimpleNamespace(
            max_upload_bytes=1_000_000,
            max_output_chars=4_000,
            sandbox_max_files=20,
            sandbox_max_expanded_bytes=1_000_000,
            admin_alerts=False,
            sandbox_enabled=False,
        )
        handlers._gate = AsyncMock(return_value=True)
        handlers._download = AsyncMock(return_value=_project_zip())
        handlers._profile = AsyncMock(return_value=SimpleNamespace(local_only=False))
        handlers._request_allowed = AsyncMock(return_value=42)
        handlers._reply_text = AsyncMock()
        handlers.db = SimpleNamespace(
            get_setting=AsyncMock(side_effect=lambda key: {
                "learning_paused": "off",
                "evolution_mode": mode,
                "current_model": "local::test-model",
            }[key]),
            finish_usage=AsyncMock(),
            create_evolution_proposal=AsyncMock(return_value=7),
        )
        handlers.router = SimpleNamespace(
            default_model=lambda: "local::test-model",
            estimate_cost=AsyncMock(return_value=0.0),
            complete=AsyncMock(return_value=AssistantResult(
                _patch().decode(), "local::test-model",
            )),
        )
        handlers.actions = SimpleNamespace(sandbox=None)
        document = SimpleNamespace(file_name="project.zip", file_id="file-id")
        message = SimpleNamespace(
            reply_to_message=SimpleNamespace(document=document),
            reply_text=AsyncMock(),
            reply_document=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type="private"),
            effective_user=SimpleNamespace(id=101, full_name="Owner"),
        )

        await handlers.patch_project(update, SimpleNamespace(args=["change"]))

        handlers.router.complete.assert_awaited_once()
        message.reply_document.assert_awaited_once()
        caption = message.reply_document.await_args.kwargs["caption"]
        if expected_stored:
            handlers.db.create_evolution_proposal.assert_awaited_once()
            assert isinstance(handlers.db.create_evolution_proposal.await_args.args[2], bytes)
            assert "Evolution proposal #7" in caption
        else:
            handlers.db.create_evolution_proposal.assert_not_awaited()
            assert "not stored or staged" in caption

    asyncio.run(scenario())
