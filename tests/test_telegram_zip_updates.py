from __future__ import annotations

import asyncio
import io
import stat
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from enan_ultra.handlers import BotHandlers
from enan_ultra.services.telegram_zip_updates import TelegramZipUpdateManager

ADMIN_ID = 1234
ADMIN_CHAT = 9876
VALID_FILES = {
    "src/enan_ultra/__init__.py": "",
    "src/enan_ultra/runtime.py": "def start():\n    return True\n",
    "tests/test_sample.py": "def test_sample():\n    assert True\n",
}


def _manager(
    tmp_path: Path, *, sandbox_enabled: bool = True, deployment_enabled: bool = False,
) -> TelegramZipUpdateManager:
    project = tmp_path / "live"
    (project / "src" / "enan_ultra").mkdir(parents=True)
    (project / "src" / "enan_ultra" / "__init__.py").write_bytes(b"# old\n")
    (project / "src" / "enan_ultra" / "runtime.py").write_bytes(b"# old runtime\n")
    settings = SimpleNamespace(
        effective_admin_user_ids=frozenset({ADMIN_ID}),
        admin_chat_id=ADMIN_CHAT,
        max_upload_bytes=2_000_000,
        max_archive_expanded_bytes=2_000_000,
        update_max_files=250,
        update_max_depth=8,
        update_max_path_length=240,
        update_max_compression_ratio=100,
        sandbox_enabled=sandbox_enabled,
        update_deployment_enabled=deployment_enabled,
        update_deployment_socket_path=tmp_path / "deploy.sock",
    )
    return TelegramZipUpdateManager(settings, project, tmp_path / "state" / ".updates")


def _zip(
    files: dict[str, str],
    *,
    mode: int | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            if mode is not None and name.endswith("runtime.py"):
                info.external_attr = mode << 16
            archive.writestr(info, content)
    return stream.getvalue()


class _PassingSandbox:
    async def run_python_tests(self, _archive_data: bytes) -> SimpleNamespace:
        return SimpleNamespace(exit_code=0, output="2 passed", timed_out=False)


class _FailingSandbox:
    async def run_python_tests(self, _archive_data: bytes) -> SimpleNamespace:
        return SimpleNamespace(exit_code=1, output="1 failed", timed_out=False)


def _prepare(manager: TelegramZipUpdateManager, data: bytes | None = None):
    manager.start_upload(ADMIN_ID, ADMIN_CHAT)
    return manager.prepare(ADMIN_ID, ADMIN_CHAT, "release.zip", data or _zip(VALID_FILES))


def test_admin_chat_scope_and_upload_expiry(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(PermissionError):
        manager.start_upload(999, ADMIN_CHAT)
    with pytest.raises(PermissionError):
        manager.start_upload(ADMIN_ID, ADMIN_CHAT + 1)

    session = manager.start_upload(ADMIN_ID, ADMIN_CHAT)
    manager.upload_sessions[ADMIN_ID] = session.__class__(
        session.user_id, session.chat_id, time.monotonic() - 1,
    )
    assert not manager.has_upload_session(ADMIN_ID)
    with pytest.raises(ValueError, match="No active"):
        manager.prepare(ADMIN_ID, ADMIN_CHAT, "release.zip", _zip(VALID_FILES))


def test_wrong_chat_upload_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.start_upload(ADMIN_ID, ADMIN_CHAT)
    with pytest.raises(PermissionError, match="ADMIN_CHAT_ID"):
        manager.prepare(ADMIN_ID, ADMIN_CHAT + 1, "release.zip", _zip(VALID_FILES))
    assert not list((tmp_path / "state").glob("**/release"))


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("../src/enan_ultra/runtime.py", "pass"),
        ("/src/enan_ultra/runtime.py", "pass"),
        ("src\\enan_ultra\\runtime.py", "pass"),
        ("src/enan_ultra/../runtime.py", "pass"),
    ],
)
def test_rejects_traversal_and_absolute_paths(
    tmp_path: Path, filename: str, contents: str,
) -> None:
    manager = _manager(tmp_path)
    files = dict(VALID_FILES)
    files.pop("src/enan_ultra/runtime.py")
    files[filename] = contents
    archive_data = _zip(files)
    if "\\" in filename:
        archive_data = archive_data.replace(b"src/enan_ultra/runtime.py", b"src\\enan_ultra\\runtime.py")
    with pytest.raises(ValueError, match="path|unsafe"):
        _prepare(manager, archive_data)
    assert not (tmp_path / "state" / ".updates").exists()


def test_rejects_invalid_zip_duplicates_case_collisions_and_symlinks(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match="not a readable ZIP"):
        _prepare(manager, b"not a zip")

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("src/enan_ultra/__init__.py", "")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("src/enan_ultra/__init__.py", "changed")
        archive.writestr("src/enan_ultra/runtime.py", "pass\n")
    with pytest.raises(ValueError, match="Duplicate"):
        _prepare(manager, duplicate.getvalue())

    collision = dict(VALID_FILES)
    collision["src/enan_ultra/Runtime.py"] = "pass\n"
    with pytest.raises(ValueError, match="case-colliding"):
        _prepare(manager, _zip(collision))

    symlink = dict(VALID_FILES)
    symlink["src/enan_ultra/evil.py"] = "outside"
    with pytest.raises(ValueError, match="symlink|special file"):
        _prepare(manager, _zip(symlink, mode=(stat.S_IFLNK | 0o777)))

    with pytest.raises(ValueError, match="compression methods"):
        _prepare(manager, _zip(VALID_FILES, compression=zipfile.ZIP_LZMA))

    encrypted = bytearray(_zip(VALID_FILES, compression=zipfile.ZIP_STORED))
    central_directory = encrypted.find(b"PK\x01\x02")
    assert central_directory >= 0
    encrypted[central_directory + 8] |= 1
    with pytest.raises(ValueError, match="Encrypted"):
        _prepare(manager, bytes(encrypted))


def test_rejects_case_collision_between_explicit_directory_and_implicit_parent(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w") as archive:
        for name, content in VALID_FILES.items():
            archive.writestr(name, content)
        archive.writestr("src/enan_ultra/Code/", "")
        archive.writestr("src/enan_ultra/code/extra.py", "pass\n")

    with pytest.raises(ValueError, match="case-colliding directory"):
        _prepare(manager, archive_data.getvalue())

    assert not (tmp_path / "state" / ".updates").exists()
    assert (tmp_path / "live" / "src" / "enan_ultra" / "runtime.py").read_text() == "# old runtime\n"


@pytest.mark.parametrize(
    "protected_path",
    [
        ".env", ".env.production", "data/assistant.sqlite3", "uploads/user.bin",
        "deploy/bot.service", "src/enan_ultra/credentials.txt",
        "src/enan_ultra/__pycache__/module.pyc", "src/enan_ultra/private.bin",
    ],
)
def test_rejects_secrets_persistent_data_and_non_allowlisted_paths(
    tmp_path: Path, protected_path: str,
) -> None:
    manager = _manager(tmp_path)
    files = dict(VALID_FILES)
    files[protected_path] = "must remain protected"
    with pytest.raises(ValueError, match="protected|allowlist"):
        _prepare(manager, _zip(files))
    assert (tmp_path / "live" / "src" / "enan_ultra" / "runtime.py").read_text() == "# old runtime\n"


def test_rejects_compression_ratio_and_corrupt_crc_before_staging(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    bomb = dict(VALID_FILES)
    bomb["src/enan_ultra/repeated.py"] = "A" * 20_000
    with pytest.raises(ValueError, match="COMPRESSION_RATIO"):
        _prepare(manager, _zip(bomb))
    assert not (tmp_path / "state" / ".updates").exists()

    archive_bytes = bytearray(_zip(VALID_FILES, compression=zipfile.ZIP_STORED))
    needle = b"def start():"
    offset = archive_bytes.index(needle)
    archive_bytes[offset] ^= 1
    with pytest.raises(ValueError, match="corrupt|size"):
        _prepare(manager, bytes(archive_bytes))
    assert not (tmp_path / "state" / ".updates").exists()


def test_stages_in_isolation_and_hash_binds_exact_release(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    staged = _prepare(manager)
    assert staged.syntax_status == "PASS"
    assert staged.test_status == "NOT RUN"
    assert (staged.release_dir / "src" / "enan_ultra" / "runtime.py").read_text() == VALID_FILES[
        "src/enan_ultra/runtime.py"
    ]
    live_file = tmp_path / "live" / "src" / "enan_ultra" / "runtime.py"
    assert live_file.read_text() == "# old runtime\n"
    assert {item.action for item in staged.files} >= {"changed", "added"}
    assert staged.release_hash in manager.manifest_text(staged)

    asyncio.run(manager.run_tests(staged, _PassingSandbox()))
    assert staged.test_status == "PASS"
    manager.decide(staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True)
    with pytest.raises(ValueError, match="already handled"):
        manager.decide(staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True)
    assert live_file.read_text() == "# old runtime\n"


def test_only_one_active_release_can_consume_staging_space(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = _prepare(manager)
    manager.start_upload(ADMIN_ID, ADMIN_CHAT)
    with pytest.raises(ValueError, match="already staged"):
        manager.prepare(ADMIN_ID, ADMIN_CHAT, "second.zip", _zip(VALID_FILES))
    assert manager.has_upload_session(ADMIN_ID)
    manager.cancel_staged(first.update_id, first.release_hash[:32], ADMIN_ID, ADMIN_CHAT)


def test_blocked_or_failed_sandbox_prevents_approval(tmp_path: Path) -> None:
    manager = _manager(tmp_path, sandbox_enabled=False)
    staged = _prepare(manager)
    asyncio.run(manager.run_tests(staged, None))
    assert staged.test_status == "BLOCKED"
    with pytest.raises(ValueError, match="blocked"):
        manager.decide(staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True)

    manager2 = _manager(tmp_path / "other")
    staged2 = _prepare(manager2)
    asyncio.run(manager2.run_tests(staged2, _FailingSandbox()))
    assert staged2.test_status == "FAIL"
    with pytest.raises(ValueError, match="blocked"):
        manager2.decide(staged2.update_id, staged2.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True)


def test_enforces_configured_upload_expansion_count_depth_and_path_limits(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.settings.max_upload_bytes = 10
    with pytest.raises(ValueError, match="MAX_UPLOAD_BYTES"):
        _prepare(manager)

    manager = _manager(tmp_path / "expanded")
    manager.settings.max_archive_expanded_bytes = 10
    with pytest.raises(ValueError, match="MAX_ARCHIVE_EXPANDED_BYTES"):
        _prepare(manager)

    manager = _manager(tmp_path / "count")
    manager.settings.update_max_files = 2
    with pytest.raises(ValueError, match="UPDATE_MAX_FILES"):
        _prepare(manager)

    nested = dict(VALID_FILES)
    nested["src/enan_ultra/nested/deep.py"] = "pass\n"
    manager = _manager(tmp_path / "depth")
    manager.settings.update_max_depth = 2
    with pytest.raises(ValueError, match="UPDATE_MAX_DEPTH"):
        _prepare(manager, _zip(nested))

    long_name = dict(VALID_FILES)
    long_name[f"src/enan_ultra/{'a' * 64}.py"] = "pass\n"
    manager = _manager(tmp_path / "path")
    manager.settings.update_max_path_length = 40
    with pytest.raises(ValueError, match="UPDATE_MAX_PATH_LENGTH"):
        _prepare(manager, _zip(long_name))


def test_unauthorized_and_replayed_telegram_approval_callbacks_are_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    staged = _prepare(manager)
    asyncio.run(manager.run_tests(staged, _PassingSandbox()))

    handler = BotHandlers.__new__(BotHandlers)
    handler.update_manager = manager
    handler.settings = SimpleNamespace(
        effective_admin_user_ids=frozenset({ADMIN_ID}), admin_chat_id=ADMIN_CHAT,
    )
    handler.db = SimpleNamespace(is_user_blocked=AsyncMock(return_value=False), audit=AsyncMock())

    def callback(user_id: int, chat_id: int):
        query = SimpleNamespace(
            data=f"zipupd:approve:{staged.update_id}:{staged.release_hash[:32]}",
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(id=user_id),
        )
        return query, update

    async def scenario() -> None:
        query, update = callback(999, ADMIN_CHAT)
        await handler.update_callback(update, None)
        query.answer.assert_awaited_once_with("Unauthorized update action.", show_alert=True)
        assert staged.state == "review"

        query, update = callback(ADMIN_ID, ADMIN_CHAT + 1)
        await handler.update_callback(update, None)
        query.answer.assert_awaited_once_with("Unauthorized update action.", show_alert=True)
        assert staged.state == "review"

        query, update = callback(ADMIN_ID, ADMIN_CHAT)
        await handler.update_callback(update, None)
        query.answer.assert_awaited_once_with()
        assert staged.state == "approved"

        replay, replay_update = callback(ADMIN_ID, ADMIN_CHAT)
        await handler.update_callback(replay_update, None)
        replay.answer.assert_awaited_once()
        assert replay.answer.call_args.kwargs["show_alert"] is True

    asyncio.run(scenario())


def test_staged_hash_tampering_and_replayed_or_unauthorized_approval_fail(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    staged = _prepare(manager)
    asyncio.run(manager.run_tests(staged, _PassingSandbox()))
    with pytest.raises(PermissionError):
        manager.decide(staged.update_id, staged.release_hash[:32], 999, ADMIN_CHAT, approve=True)
    with pytest.raises(ValueError, match="exact staged release"):
        manager.decide(staged.update_id, "0" * 12, ADMIN_ID, ADMIN_CHAT, approve=True)

    (staged.release_dir / "src" / "enan_ultra" / "runtime.py").write_text("changed\n")
    with pytest.raises(ValueError, match="content changed"):
        manager.decide(staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True)


def test_reject_cancel_and_expiry_remove_only_staged_files(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    rejected = _prepare(manager)
    asyncio.run(manager.run_tests(rejected, _PassingSandbox()))
    manager.decide(rejected.update_id, rejected.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=False)
    assert not rejected.release_dir.exists()

    cancelled = _prepare(manager)
    manager.cancel_staged(cancelled.update_id, cancelled.release_hash[:32], ADMIN_ID, ADMIN_CHAT)
    assert not cancelled.release_dir.exists()

    expired = _prepare(manager)
    expired.expires_at = time.monotonic() - 1
    assert manager.get_update(expired.update_id, ADMIN_ID, ADMIN_CHAT) is None
    assert not expired.release_dir.exists()


def test_production_approval_is_separate_authorized_and_one_shot(tmp_path: Path) -> None:
    manager = _manager(tmp_path, deployment_enabled=True)
    release_files = dict(VALID_FILES)
    release_files["src/enan_ultra/runtime.py"] = "# old runtime\n"
    staged = _prepare(manager, _zip(release_files))
    asyncio.run(manager.run_tests(staged, _PassingSandbox()))
    prefix = staged.release_hash[:32]

    with pytest.raises(ValueError, match="staging-approved"):
        manager.begin_production_deployment(
            staged.update_id, prefix, ADMIN_ID, ADMIN_CHAT,
        )
    with pytest.raises(PermissionError):
        manager.production_eligibility(staged.update_id, prefix, 999, ADMIN_CHAT)

    manager.decide(staged.update_id, prefix, ADMIN_ID, ADMIN_CHAT, approve=True)
    approved, issues = manager.production_eligibility(
        staged.update_id, prefix, ADMIN_ID, ADMIN_CHAT,
    )
    assert not issues
    assert approved.test_status == "PASS"
    request = manager.begin_production_deployment(
        staged.update_id, prefix, ADMIN_ID, ADMIN_CHAT,
    )
    assert request.production_approved_by == ADMIN_ID
    assert request.deployment_state == "submitting"
    manager.finish_production_deployment(
        staged.update_id, accepted=True, result="Accepted.",
    )
    with pytest.raises(ValueError, match="already been used"):
        manager.begin_production_deployment(
            staged.update_id, prefix, ADMIN_ID, ADMIN_CHAT,
        )


def test_production_approval_rejects_dependency_and_control_file_changes(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, deployment_enabled=True)
    (manager.project_root / "pyproject.toml").write_text("[project]\nname='old'\n")
    staged_files = dict(VALID_FILES)
    staged_files["pyproject.toml"] = "[project]\nname='new'\n"
    staged_files["src/enan_ultra/handlers.py"] = "# untrusted policy change\n"
    staged = _prepare(manager, _zip(staged_files))
    asyncio.run(manager.run_tests(staged, _PassingSandbox()))
    manager.decide(
        staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True,
    )

    _, issues = manager.production_eligibility(
        staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT,
    )

    assert any("Dependency changes" in issue for issue in issues)
    assert any("handlers.py" in issue for issue in issues)


def test_test_summary_redacts_credential_assignments(tmp_path: Path) -> None:
    class _SecretSandbox:
        async def run_python_tests(self, _archive_data: bytes) -> SimpleNamespace:
            return SimpleNamespace(
                exit_code=1,
                output=(
                    "OPENROUTER_API_KEY=unit-test-placeholder "
                    "TELEGRAM_BOT_TOKEN=unit-test-placeholder"
                ),
                timed_out=False,
            )

    manager = _manager(tmp_path)
    staged = _prepare(manager)
    asyncio.run(manager.run_tests(staged, _SecretSandbox()))

    assert "OPENROUTER_API_KEY=unit-test-placeholder" not in staged.test_summary
    assert "TELEGRAM_BOT_TOKEN=unit-test-placeholder" not in staged.test_summary

def test_production_callback_authorizes_exact_release_and_rejects_replay(
    tmp_path: Path, monkeypatch,
) -> None:
    manager = _manager(tmp_path, deployment_enabled=True)
    release_files = dict(VALID_FILES)
    release_files["src/enan_ultra/runtime.py"] = "# old runtime\n"
    staged = _prepare(manager, _zip(release_files))
    asyncio.run(manager.run_tests(staged, _PassingSandbox()))
    manager.decide(
        staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True,
    )
    handler = BotHandlers.__new__(BotHandlers)
    handler.update_manager = manager
    handler.settings = SimpleNamespace(
        effective_admin_user_ids=frozenset({ADMIN_ID}), admin_chat_id=ADMIN_CHAT,
    )
    handler.db = SimpleNamespace(is_user_blocked=AsyncMock(return_value=False), audit=AsyncMock())

    class _AcceptedDeployment:
        def __init__(self, _path) -> None:
            pass

        async def deploy(self, *args):
            assert args == (
                staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
            )
            return {"ok": True, "accepted": True}

    monkeypatch.setattr(
        "enan_ultra.handlers.TelegramDeploymentClient", _AcceptedDeployment,
    )

    def callback(user_id: int, chat_id: int):
        query = SimpleNamespace(
            data=f"zipupd:deploy:{staged.update_id}:{staged.release_hash[:32]}",
            answer=AsyncMock(),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(id=user_id),
        )
        return query, update

    async def scenario() -> None:
        unauthorized, unauthorized_update = callback(999, ADMIN_CHAT)
        await handler.update_callback(unauthorized_update, None)
        unauthorized.answer.assert_awaited_once_with(
            "Unauthorized update action.", show_alert=True,
        )
        assert staged.deployment_state == "not_requested"

        query, update = callback(ADMIN_ID, ADMIN_CHAT)
        await handler.update_callback(update, None)
        query.answer.assert_awaited_once_with()
        assert staged.deployment_state == "deploying"
        assert handler.db.audit.await_count == 2

        replay, replay_update = callback(ADMIN_ID, ADMIN_CHAT)
        await handler.update_callback(replay_update, None)
        replay.answer.assert_awaited_once()
        assert replay.answer.call_args.kwargs["show_alert"] is True
        assert staged.deployment_state == "deploying"

    asyncio.run(scenario())
