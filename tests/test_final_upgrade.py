from __future__ import annotations

import asyncio
import io
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
from enan_ultra.services.providers import ProviderRouter
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
            assert [row[0] for row in versions][-1] == 3
        finally:
            await db.close()

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
        source = Database(source_path, data_key=KEY)
        await source.open()
        try:
            document_id = await source.save_document(
                500, "plan.txt", "text/plain", [("page 1", "restoreable knowledge")],
            )
            await source.promote_document_to_kb(9, 500, document_id)
            await source.backup_to(backup_path)
        finally:
            await source.close()
        restored = Database(backup_path, data_key=KEY)
        await restored.open()
        try:
            assert (await restored.search_kb(9, "restoreable"))[0]["content"] == "restoreable knowledge"
            assert await restored.search_kb(10, "restoreable") == []
        finally:
            await restored.close()

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
        router.settings = SimpleNamespace(provider_order=("openai", "ollama"), fallback_models=())
        router.openrouter = None
        cloud = _FakeAdapter("openai", "cloud-model", fail=True)
        local = _FakeAdapter("ollama", "local-model")
        router.adapters = {"openai": cloud, "ollama": local}
        result = await router.complete([], primary_model="openai::cloud-model", local_only=False)
        assert result.text == "routed"
        assert cloud.calls == 1 and local.calls == 1

        cloud.calls = local.calls = 0
        result = await router.complete([], primary_model="openai::cloud-model", local_only=True)
        assert result.model == "ollama::local-model"
        assert cloud.calls == 0 and local.calls == 1

    asyncio.run(scenario())


def test_settings_reject_missing_credentials_and_public_dashboard(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    required = {
        "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "ADMIN_CHAT_ID": "123456",
        "ADMIN_USER_IDS": "123456",
        "OPENAI_API_KEY": "test-only-provider-key",
        "OPENAI_MODEL": "test-model",
        "APP_DATA_KEY": KEY,
        "DASHBOARD_AUTH_TOKEN": "test-only-dashboard-auth-token-1234567890abcdef",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    settings = Settings.load()
    assert settings.persona_profile == "GPT-6 Astra"
    assert settings.dashboard_host == "127.0.0.1"

    monkeypatch.setenv("DASHBOARD_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="127.0.0.1"):
        Settings.load()


def test_deployment_env_generates_and_preserves_secrets(tmp_path: Path, monkeypatch, capsys) -> None:
    from scripts import prepare_deploy_env

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
                   .replace("OPENROUTER_API_KEY=", "OPENROUTER_API_KEY=test-only-key"), encoding="utf-8")
    assert prepare_deploy_env.main() == 0
    second = path.read_text(encoding="utf-8")
    assert f"APP_DATA_KEY={values['APP_DATA_KEY']}" in second
    assert f"DASHBOARD_AUTH_TOKEN={values['DASHBOARD_AUTH_TOKEN']}" in second


def test_dashboard_requires_login_and_same_origin() -> None:
    class FakeDb:
        async def active_live_count(self):
            return 0

    settings = SimpleNamespace(
        dashboard_host="127.0.0.1", dashboard_port=8787,
        dashboard_auth_token="dashboard-test-secret-with-more-than-32-characters",
        effective_admin_user_ids=frozenset({123}),
    )
    dashboard = AdminDashboard(settings, FakeDb(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace())

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
            assert (await client.post("/api/v1/approvals/1/approve", json={})).status_code == 403
            assert (await client.post("/auth/logout", headers={"Origin": "http://127.0.0.1"})).status_code == 200

    asyncio.run(scenario())
