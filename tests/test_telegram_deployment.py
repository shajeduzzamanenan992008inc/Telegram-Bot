from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import deploy.telegram_ai_deploy as deployment_module
from deploy.telegram_ai_deploy import (
    DeploymentError,
    DeploymentPaths,
    DeploymentRequestHandler,
    ReleaseDeployer,
    parse_auto_deploy_admin_ids,
)
from enan_ultra.runtime import _deployment_health_marker_path
from enan_ultra.services.telegram_deployment import TelegramDeploymentClient
from enan_ultra.services.telegram_zip_updates import TelegramZipUpdateManager

ADMIN_ID = 1234
ADMIN_CHAT = 9876


class _PassingSandbox:
    async def run_python_tests(self, _archive_data: bytes) -> SimpleNamespace:
        return SimpleNamespace(exit_code=0, output="3 passed", timed_out=False)


class _FailingSandbox:
    async def run_python_tests(self, _archive_data: bytes) -> SimpleNamespace:
        return SimpleNamespace(exit_code=1, output="1 failed", timed_out=False)


class _Clock:
    def __init__(self) -> None:
        self.value = 1_800_000_000.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += duration


class _Systemd:
    def __init__(
        self, app_root: Path, environment_file: Path, health_marker: Path, clock: _Clock,
    ) -> None:
        self.app_root = app_root
        self.environment_file = environment_file
        self.health_marker = health_marker
        self.clock = clock
        self.pid = 100
        self.restart_count = 0
        self.fail_restart_numbers: set[int] = set()
        self.fail_health_numbers: set[int] = set()

    def show(self) -> dict[str, str]:
        python = str(self.app_root / ".venv" / "bin" / "python")
        return {
            "User": "enan-ultra",
            "Group": "enan-ultra",
            "WorkingDirectory": str(self.app_root / "current"),
            "EnvironmentFiles": f"{self.environment_file} (ignore_errors=no)",
            "ExecStart": f"{{ path={python} ; argv[]={python} -m enan_ultra }}",
            "ActiveState": "active",
            "MainPID": str(self.pid),
        }

    def restart(self) -> None:
        self.restart_count += 1
        if self.restart_count in self.fail_restart_numbers:
            raise DeploymentError("service_restart_failed")
        self.pid += 1
        if self.restart_count not in self.fail_health_numbers:
            release = _active_release(self.app_root)
            self.health_marker.write_text(json.dumps({
                "pid": self.pid,
                "started_at": self.clock(),
                "database_open": True,
                "telegram_bot_id": 987654,
                "import_origin": str((release / "src" / "enan_ultra" / "runtime.py").resolve()),
            }), encoding="utf-8")


def _active_release(app_root: Path) -> Path:
    if os.name == "nt":
        target = (app_root / ".current-release").read_text(encoding="utf-8").strip()
    else:
        target = os.readlink(app_root / "current")
    return (app_root / Path(*PurePosixPath(target).parts)).resolve()


def _make_release(
    tmp_path: Path,
    *,
    test_sandbox: object | None = None,
    additional_files: dict[str, str] | None = None,
) -> tuple[TelegramZipUpdateManager, object]:
    app_root = tmp_path / "live"
    release_root = app_root / "releases" / "initial"
    package = release_root / "src" / "enan_ultra"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"# baseline\n")
    (package / "runtime.py").write_bytes(b"def run():\n    return True\n")
    (package / "worker.py").write_bytes(b"VALUE = 'old'\n")
    (package / "handlers.py").write_bytes(b"# existing policy file\n")
    settings = SimpleNamespace(
        admin_id=ADMIN_ID,
        effective_admin_user_ids=frozenset({ADMIN_ID}),
        admin_chat_id=ADMIN_CHAT,
        max_upload_bytes=2_000_000,
        max_archive_expanded_bytes=2_000_000,
        update_max_files=250,
        update_max_depth=8,
        update_max_path_length=240,
        update_max_compression_ratio=100,
        sandbox_enabled=True,
        update_deployment_enabled=True,
        update_deployment_socket_path=Path("/run/telegram-ai-deploy.sock"),
    )
    manager = TelegramZipUpdateManager(
        settings, release_root, tmp_path / "bot-state" / ".telegram-zip-updates",
    )
    files = {
        "src/enan_ultra/__init__.py": "# baseline\n",
        "src/enan_ultra/runtime.py": "def run():\n    return True\n",
        "src/enan_ultra/handlers.py": "# existing policy file\n",
        "src/enan_ultra/worker.py": "VALUE = 'new'\n",
        "src/enan_ultra/feature.py": "FEATURE_ENABLED = True\n",
        "tests/test_sample.py": "def test_sample():\n    assert True\n",
    }
    if additional_files:
        files.update(additional_files)
    import io

    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    manager.start_upload(ADMIN_ID, ADMIN_CHAT)
    staged = manager.prepare(
        ADMIN_ID, ADMIN_CHAT, "release.zip", archive_data.getvalue(),
    )
    if test_sandbox is not None:
        asyncio.run(manager.run_tests(staged, test_sandbox))
    return manager, staged


def _make_deployer(
    tmp_path: Path,
    manager: TelegramZipUpdateManager,
    *,
    health_fail_numbers: set[int] | None = None,
    restart_fail_numbers: set[int] | None = None,
    auto_deploy_admin_ids: frozenset[int] = frozenset(),
) -> tuple[ReleaseDeployer, _Systemd, DeploymentPaths]:
    app_root = manager.project_root.parents[1]
    if os.name == "nt":
        (app_root / ".current-release").write_text("releases/initial", encoding="utf-8")
    else:
        (app_root / "current").symlink_to("releases/initial", target_is_directory=True)
    state_root = tmp_path / "root-deploy-state"
    backup_root = state_root / "release-backups"
    history_root = state_root / "deployment-history"
    for path in (state_root, backup_root, history_root):
        path.mkdir(parents=True, exist_ok=True)
    enable_flag = tmp_path / "operator-enabled"
    enable_flag.write_text("enabled by test operator\n", encoding="utf-8")
    health_marker = tmp_path / "bot-state" / "runtime-health.json"
    health_marker.parent.mkdir(parents=True, exist_ok=True)
    clock = _Clock()
    environment_file = tmp_path / "bot.env"
    environment_file.write_text("CONFIGURED_BY_TEST=1\n", encoding="utf-8")
    if os.name != "nt":
        environment_file.chmod(0o600)
    systemd = _Systemd(app_root, environment_file, health_marker, clock)
    health_marker.write_text(json.dumps({
        "pid": systemd.pid,
        "started_at": 1_799_999_990.0,
        "database_open": True,
        "telegram_bot_id": 987654,
        "import_origin": str((manager.project_root / "src" / "enan_ultra" / "runtime.py").resolve()),
    }), encoding="utf-8")
    systemd.fail_health_numbers = set(health_fail_numbers or ())
    systemd.fail_restart_numbers = set(restart_fail_numbers or ())
    paths = DeploymentPaths(
        app_root=app_root,
        current_release=app_root / "current",
        releases_root=app_root / "releases",
        environment_file=environment_file,
        state_root=state_root,
        staging_root=manager.staging_parent,
        backup_root=backup_root,
        history_root=history_root,
        authorization_db=state_root / "deployment-authorizations.sqlite3",
        health_marker=health_marker,
        lock_path=state_root / "deployment.lock",
        enable_flag=enable_flag,
    )
    deployer = ReleaseDeployer(
        paths, systemd=systemd, require_root=False, health_timeout=2,
        sleep=clock.sleep, clock=clock,
        auto_deploy_admin_ids=auto_deploy_admin_ids,
    )
    return deployer, systemd, paths


def _approve_staging(manager: TelegramZipUpdateManager, staged: object) -> None:
    manager.decide(
        staged.update_id, staged.release_hash[:32], ADMIN_ID, ADMIN_CHAT, approve=True,
    )


def _deploy(deployer: ReleaseDeployer, staged: object) -> dict[str, object]:
    return deployer.deploy(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )


def test_broker_requires_root_one_time_authorization_after_telegram_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    claimed_admin_id = ADMIN_ID + 1  # A positive forged claim is not authorization.
    bot_uid = 1001
    root = SimpleNamespace(deployer=deployer, allowed_uid=bot_uid)

    queued_response = DeploymentRequestHandler._dispatch(
        root,
        {
            "operation": "request_deploy",
            "update_id": staged.update_id,
            "manifest_hash": staged.release_hash,
            "archive_hash": staged.archive_sha256,
            "claimed_administrator_id": claimed_admin_id,
        },
        peer_uid=bot_uid,
    )
    assert queued_response["accepted"] is True
    assert queued_response["state"] == "awaiting_operator_authorization"
    queued = deployer.status(staged.update_id)
    assert queued is not None
    assert queued["state"] == "awaiting_operator_authorization"
    assert queued["claimed_administrator_id"] == claimed_admin_id
    assert systemd.restart_count == 0

    details = DeploymentRequestHandler._dispatch(
        root, {"operation": "approval_details", "update_id": staged.update_id},
        peer_uid=0,
    )
    assert details["approval"]["manifest_hash"] == staged.release_hash
    assert details["approval"]["claimed_administrator_id"] == claimed_admin_id

    with pytest.raises(DeploymentError, match="unauthorized_operator"):
        DeploymentRequestHandler._dispatch(
            root, {"operation": "authorize", "update_id": staged.update_id,
                   "manifest_hash": staged.release_hash}, peer_uid=bot_uid,
        )
    with pytest.raises(DeploymentError, match="operator_approval_digest_mismatch"):
        deployer.authorize_pending_deployment(
            staged.update_id, "0" * 64, operator_uid=0,
        )
    with pytest.raises(DeploymentError, match="unauthorized_operator"):
        deployer.authorize_pending_deployment(
            staged.update_id, staged.release_hash, operator_uid=bot_uid,
        )
    assert systemd.restart_count == 0

    worker_finished = threading.Event()
    run_submitted = deployer._run_submitted

    def track_worker_completion(
        update_id: str, manifest_hash: str, archive_hash: str, administrator_id: int,
    ) -> None:
        try:
            run_submitted(update_id, manifest_hash, archive_hash, administrator_id)
        finally:
            worker_finished.set()

    monkeypatch.setattr(deployer, "_run_submitted", track_worker_completion)
    assert deployer.authorize_pending_deployment(
        staged.update_id, staged.release_hash, operator_uid=0,
    )
    assert worker_finished.wait(timeout=60), (
        f"Deployment worker did not finish within 60 seconds: {deployer.status(staged.update_id)}"
    )
    result = deployer.status(staged.update_id)
    assert result is not None
    assert result["state"] in {"succeeded", "failed", "rolled_back", "rollback_failed"}, result
    assert result["state"] == "succeeded", result
    assert result["archive_hash"] == staged.archive_sha256
    assert result["backup_id"] == f"20270115T080000Z-{staged.update_id}"
    assert result["validation"] == {"syntax": "PASS", "tests": "PASS"}
    assert "snapshot" not in result
    assert systemd.restart_count == 1
    assert not deployer._activation_scheduled
    with pytest.raises(DeploymentError, match="operator_approval_missing_or_used"):
        deployer.authorize_pending_deployment(
            staged.update_id, staged.release_hash, operator_uid=0,
        )
    assert not deployer.request_deployment(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )

    audit = (deployer.paths.history_root / "audit.jsonl").read_text(encoding="utf-8")
    assert '"approval_method":"root_unix_peer_and_interactive_operator"' in audit
    assert '"operator_uid":0' in audit
    assert "TOKEN=" not in audit


def test_admin_auto_deploy_policy_is_disabled_unless_root_configured(tmp_path: Path) -> None:
    assert parse_auto_deploy_admin_ids("") == frozenset()
    assert parse_auto_deploy_admin_ids("1234, 5678,1234") == frozenset({1234, 5678})
    for invalid in ("0", "-1234", "1234,,5678", "１２３４", ",".join(str(i + 1) for i in range(101))):
        with pytest.raises(ValueError, match="TELEGRAM_AI_AUTO_DEPLOY_ADMIN_IDS"):
            parse_auto_deploy_admin_ids(invalid)

    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(
        tmp_path, manager, auto_deploy_admin_ids=frozenset({ADMIN_ID}),
    )
    response = DeploymentRequestHandler._dispatch(
        SimpleNamespace(deployer=deployer, allowed_uid=1001),
        {
            "operation": "request_deploy",
            "update_id": staged.update_id,
            "manifest_hash": staged.release_hash,
            "archive_hash": staged.archive_sha256,
            "claimed_administrator_id": ADMIN_ID,
        },
        peer_uid=1001,
    )
    assert response["accepted"] is True
    assert response["state"] == "queued"
    assert response["authorization_method"] == "telegram_admin_allowlist_auto_deploy"

    deadline = time.monotonic() + 5
    result = deployer.status(staged.update_id)
    while result and result["state"] not in {
        "succeeded", "failed", "rolled_back", "rollback_failed",
    } and time.monotonic() < deadline:
        time.sleep(0.01)
        result = deployer.status(staged.update_id)
    assert result is not None and result["state"] == "succeeded", result
    assert result["archive_hash"] == staged.archive_sha256
    assert result["backup_id"] == f"20270115T080000Z-{staged.update_id}"
    assert result["validation"] == {"syntax": "PASS", "tests": "PASS"}
    audit = (deployer.paths.history_root / "audit.jsonl").read_text(encoding="utf-8")
    assert '"approval_method":"telegram_admin_allowlist_auto_deploy"' in audit
    assert systemd.restart_count == 1


def test_admin_auto_deploy_policy_never_authorizes_unlisted_admin(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(
        tmp_path, manager, auto_deploy_admin_ids=frozenset({ADMIN_ID + 1}),
    )
    response = DeploymentRequestHandler._dispatch(
        SimpleNamespace(deployer=deployer, allowed_uid=1001),
        {
            "operation": "request_deploy",
            "update_id": staged.update_id,
            "manifest_hash": staged.release_hash,
            "archive_hash": staged.archive_sha256,
            "claimed_administrator_id": ADMIN_ID,
        },
        peer_uid=1001,
    )
    assert response["accepted"] is True
    assert response["state"] == "awaiting_operator_authorization"
    assert deployer.status(staged.update_id)["state"] == "awaiting_operator_authorization"
    assert systemd.restart_count == 0


def test_admin_auto_deploy_preserves_backup_and_health_rollback(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(
        tmp_path, manager, health_fail_numbers={1},
        auto_deploy_admin_ids=frozenset({ADMIN_ID}),
    )
    assert deployer.request_deployment(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )

    deadline = time.monotonic() + 5
    result = deployer.status(staged.update_id)
    while result and result["state"] not in {
        "succeeded", "failed", "rolled_back", "rollback_failed",
    } and time.monotonic() < deadline:
        time.sleep(0.01)
        result = deployer.status(staged.update_id)
    assert result is not None and result["state"] == "rolled_back", result
    assert result["archive_hash"] == staged.archive_sha256
    assert result["backup_id"] == f"20270115T080000Z-{staged.update_id}"
    assert systemd.restart_count == 2


def test_admin_auto_deploy_rejects_a_changed_digest_before_restart(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(
        tmp_path, manager, auto_deploy_admin_ids=frozenset({ADMIN_ID}),
    )
    assert deployer.request_deployment(
        staged.update_id, staged.release_hash, "0" * 64, ADMIN_ID,
    )

    deadline = time.monotonic() + 5
    result = deployer.status(staged.update_id)
    while result and result["state"] not in {
        "succeeded", "failed", "rolled_back", "rollback_failed",
    } and time.monotonic() < deadline:
        time.sleep(0.01)
        result = deployer.status(staged.update_id)
    assert result is not None and result["state"] == "failed", result
    assert result["failure_code"] == "archive_hash_mismatch"
    assert systemd.restart_count == 0


def test_broker_protocol_rejects_wrong_peer_legacy_admin_field_and_unknown_operation(
    tmp_path: Path,
) -> None:
    manager, _staged = _make_release(tmp_path)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    handler = SimpleNamespace(deployer=deployer, allowed_uid=1001)
    request = {
        "operation": "request_deploy",
        "update_id": "1234abcd",
        "manifest_hash": "a" * 64,
        "archive_hash": "b" * 64,
        "claimed_administrator_id": 1234,
    }
    with pytest.raises(DeploymentError, match="unauthorized_service_peer"):
        DeploymentRequestHandler._dispatch(handler, request, peer_uid=2002)
    with pytest.raises(DeploymentError, match="caller_identity_field_forbidden"):
        DeploymentRequestHandler._dispatch(
            handler, {**request, "administrator_id": 1234}, peer_uid=1001,
        )
    with pytest.raises(DeploymentError, match="unknown_operation"):
        DeploymentRequestHandler._dispatch(
            handler, {"operation": "root_shell"}, peer_uid=1001,
        )
    assert systemd.restart_count == 0


def test_root_console_requires_verified_admin_id_and_full_release_digest() -> None:
    update_id = "a1b2c3d4"
    manifest_hash = "a" * 64
    approval = {
        "update_id": update_id,
        "operation": "deploy",
        "manifest_hash": manifest_hash,
        "archive_hash": "b" * 64,
        "claimed_administrator_id": ADMIN_ID,
    }
    prompts: list[str] = []
    answers = iter((str(ADMIN_ID), f"DEPLOY {update_id} {manifest_hash}"))

    confirmed = deployment_module._confirm_operator_approval(
        update_id, approval, input_fn=lambda prompt: (prompts.append(prompt), next(answers))[1],
        output_fn=lambda _line: None,
    )
    assert confirmed == manifest_hash
    assert len(prompts) == 2
    assert f"{manifest_hash}" in prompts[-1]

    with pytest.raises(SystemExit, match="administrator ID did not match"):
        deployment_module._confirm_operator_approval(
            update_id, approval,
            input_fn=lambda _prompt: "999999",
            output_fn=lambda _line: None,
        )
    with pytest.raises(SystemExit, match="Exact release confirmation did not match"):
        deployment_module._confirm_operator_approval(
            update_id, approval,
            input_fn=lambda _prompt: str(ADMIN_ID) if "administrator" in _prompt else "DEPLOY stale",
            output_fn=lambda _line: None,
        )
    with pytest.raises(SystemExit, match="incomplete or mismatched"):
        deployment_module._confirm_operator_approval(
            update_id, {**approval, "operation": "status"},
            input_fn=lambda _prompt: "unreachable",
            output_fn=lambda _line: None,
        )


def test_broker_socket_unavailable_fails_closed(tmp_path: Path) -> None:
    async def unavailable(_path: str):
        raise FileNotFoundError("socket missing")

    client = TelegramDeploymentClient(
        tmp_path / "missing.sock", socket_connector=unavailable,
    )
    with pytest.raises(RuntimeError, match="deployment broker is unavailable"):
        asyncio.run(client.deploy("1234abcd", "a" * 64, "b" * 64, ADMIN_ID))


def test_operator_authorization_fails_closed_for_expired_or_unavailable_state(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    assert deployer.request_deployment(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )
    deployer.clock.value += deployment_module.OPERATOR_APPROVAL_TTL_SECONDS + 1
    with pytest.raises(DeploymentError, match="operator_approval_expired"):
        deployer.pending_operator_approval(staged.update_id)
    with pytest.raises(DeploymentError, match="operator_approval_missing_or_used"):
        deployer.authorize_pending_deployment(
            staged.update_id, staged.release_hash, operator_uid=0,
        )
    expired_status = deployer.status(staged.update_id)
    assert expired_status is not None
    assert expired_status["state"] == "failed"
    assert expired_status["failure_code"] == "operator_approval_expired"
    assert not deployer.request_deployment(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )
    assert systemd.restart_count == 0

    manager2, staged2 = _make_release(tmp_path / "second")
    deployer2, systemd2, paths2 = _make_deployer(tmp_path / "second", manager2)
    paths2.authorization_db.write_bytes(b"not a valid sqlite database")
    with pytest.raises(DeploymentError, match="authorization_state_unavailable"):
        deployer2.request_deployment(
            staged2.update_id, staged2.release_hash, staged2.archive_sha256, ADMIN_ID,
        )
    assert systemd2.restart_count == 0

    manager3, staged3 = _make_release(tmp_path / "third")
    deployer3, systemd3, _paths3 = _make_deployer(tmp_path / "third", manager3)
    deployer3.clock = lambda: float("nan")
    with pytest.raises(DeploymentError, match="authorization_clock_unavailable"):
        deployer3.request_deployment(
            staged3.update_id, staged3.release_hash, staged3.archive_sha256, ADMIN_ID,
        )
    assert systemd3.restart_count == 0


def test_changed_staged_candidate_fails_after_operator_approves_old_digest(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    assert deployer.request_deployment(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )

    staged.archive_path.write_bytes(b"changed after Telegram approval")
    assert deployer.authorize_pending_deployment(
        staged.update_id, staged.release_hash, operator_uid=0,
    )
    deadline = time.monotonic() + 5
    result = deployer.status(staged.update_id)
    while result and result["state"] not in {
        "succeeded", "failed", "rolled_back", "rollback_failed",
    } and time.monotonic() < deadline:
        time.sleep(0.01)
        result = deployer.status(staged.update_id)

    assert result is not None and result["state"] == "failed"
    assert result["failure_code"] == "archive_hash_mismatch"
    assert systemd.restart_count == 0


def test_concurrent_duplicate_broker_requests_create_one_pending_approval(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    manager, staged = _make_release(tmp_path)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)

    def queue() -> bool:
        try:
            return deployer.request_deployment(
                staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
            )
        except DeploymentError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _index: queue(), range(8)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert deployer.status(staged.update_id)["state"] == "awaiting_operator_authorization"
    assert systemd.restart_count == 0


def test_concurrent_operator_approval_consumes_one_authorization(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    manager, staged = _make_release(tmp_path)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    assert deployer.request_deployment(
        staged.update_id, staged.release_hash, staged.archive_sha256, ADMIN_ID,
    )
    # Keep this test focused on the cross-thread, one-use state transition.
    deployer._run_submitted = lambda *_args: None  # type: ignore[method-assign]

    def authorize_once() -> bool:
        try:
            return deployer.authorize_pending_deployment(
                staged.update_id, staged.release_hash, operator_uid=0,
            )
        except DeploymentError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: authorize_once(), range(2)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    assert systemd.restart_count == 0


def test_deploy_creates_recovery_snapshot_and_preserves_secrets_and_database(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, _systemd, paths = _make_deployer(tmp_path, manager)
    secret = paths.app_root / ".env"
    secret.write_text("TOKEN=keep\n", encoding="utf-8")
    database = paths.state_root / "application.sqlite3"
    database.write_bytes(b"database bytes")

    outcome = _deploy(deployer, staged)

    assert outcome["state"] == "succeeded"
    active_release = deployer._active_release_path()
    assert active_release.name == staged.release_hash
    assert (active_release / "src/enan_ultra/worker.py").read_text() == "VALUE = 'new'\n"
    assert (active_release / "src/enan_ultra/feature.py").is_file()
    assert (manager.project_root / "src/enan_ultra/worker.py").read_text() == "VALUE = 'old'\n"
    assert secret.read_text() == "TOKEN=keep\n"
    assert database.read_bytes() == b"database bytes"
    assert list(paths.backup_root.glob("*/snapshot.json"))
    assert json.loads((paths.history_root / "audit.jsonl").read_text().splitlines()[-1])[
        "administrator_id"
    ] == ADMIN_ID


def test_rejects_changed_manifest_and_archive_hash_before_backup(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, systemd, paths = _make_deployer(tmp_path, manager)
    staged.manifest_path.write_text("{}", encoding="utf-8")

    result = _deploy(deployer, staged)

    assert result["state"] == "failed"
    assert result["failure_code"] == "manifest_hash_mismatch"
    assert systemd.restart_count == 0
    assert not list(paths.backup_root.glob("*/snapshot.json"))


def test_deployment_uses_private_copy_of_staged_release(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, _systemd, paths = _make_deployer(tmp_path, manager)
    private_context = tempfile.TemporaryDirectory(dir=paths.state_root)
    try:
        private_update = Path(private_context.name) / staged.update_id
        private_update.mkdir(mode=0o700)
        deployer._copy_staged_release(
            staged.update_id, private_update, staged.release_hash, staged.archive_sha256,
        )
        (staged.release_dir / "src/enan_ultra/worker.py").write_bytes(b"RACED CONTENT\n")

        manifest = deployer._verify_staged_release(
            staged.update_id, staged.release_hash, staged.archive_sha256,
            update_root=private_update,
        )
        candidate = deployer._prepare_versioned_release(
            staged.update_id, staged.release_hash, manifest, private_update / "release",
        )
    finally:
        private_context.cleanup()

    assert (manager.project_root / "src/enan_ultra/worker.py").read_bytes() == b"VALUE = 'old'\n"
    assert (candidate / "src/enan_ultra/worker.py").read_bytes() == b"VALUE = 'new'\n"


def test_failed_or_blocked_validation_cannot_be_deployed(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_FailingSandbox())
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)

    result = _deploy(deployer, staged)

    assert result["state"] == "failed"
    assert result["failure_code"] == "mandatory_validation_not_passed"
    assert systemd.restart_count == 0


def test_rejects_unauthorized_or_invalid_deployment_identity(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, _systemd, _paths = _make_deployer(tmp_path, manager)

    with pytest.raises(DeploymentError, match="invalid_deployment_request"):
        deployer.deploy(staged.update_id, staged.release_hash, staged.archive_sha256, 0)


def test_backup_failure_does_not_activate_or_restart(tmp_path: Path, monkeypatch) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    before = (manager.project_root / "src/enan_ultra/worker.py").read_bytes()

    def fail_backup(_update_id: str) -> Path:
        raise OSError("sensitive path details")

    monkeypatch.setattr(deployer, "_create_snapshot", fail_backup)
    result = _deploy(deployer, staged)

    assert result["state"] == "failed"
    assert result["failure_code"] == "deployment_failed"
    assert systemd.restart_count == 0
    assert (manager.project_root / "src/enan_ultra/worker.py").read_bytes() == before


def test_concurrent_deployment_is_rejected(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, _systemd, _paths = _make_deployer(tmp_path, manager)
    deployer._job_lock.acquire()
    try:
        with pytest.raises(DeploymentError, match="deployment_already_running"):
            _deploy(deployer, staged)
    finally:
        deployer._job_lock.release()


def test_interrupted_version_switch_rolls_back_to_immutable_release(
    tmp_path: Path, monkeypatch,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    activate = deployer._activate_release
    calls = 0

    def switch_then_interrupt(release: Path) -> None:
        nonlocal calls
        activate(release)
        if calls == 0:
            calls += 1
            raise DeploymentError("activation_interrupted")

    monkeypatch.setattr(deployer, "_activate_release", switch_then_interrupt)
    result = _deploy(deployer, staged)

    assert result["state"] == "rolled_back"
    assert deployer._active_release_path().name == "initial"
    assert (manager.project_root / "src/enan_ultra/worker.py").read_bytes() == b"VALUE = 'old'\n"
    assert systemd.restart_count == 1


def test_failed_systemd_restart_rolls_back_and_verifies_old_release(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, systemd, _paths = _make_deployer(
        tmp_path, manager, restart_fail_numbers={1},
    )

    result = _deploy(deployer, staged)

    assert result["state"] == "rolled_back"
    assert systemd.restart_count == 2
    assert deployer._active_release_path().name == "initial"
    assert (manager.project_root / "src/enan_ultra/worker.py").read_bytes() == b"VALUE = 'old'\n"


def test_failed_health_check_rolls_back_and_restarts_known_good_release(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, systemd, _paths = _make_deployer(
        tmp_path, manager, health_fail_numbers={1},
    )

    result = _deploy(deployer, staged)

    assert result["state"] == "rolled_back"
    assert systemd.restart_count == 2
    assert deployer._active_release_path().name == "initial"
    assert (manager.project_root / "src/enan_ultra/worker.py").read_bytes() == b"VALUE = 'old'\n"


def test_rollback_failure_is_reported_without_claiming_recovery(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, _systemd, _paths = _make_deployer(
        tmp_path, manager, health_fail_numbers={1, 2},
    )

    result = _deploy(deployer, staged)

    assert result["state"] == "rollback_failed"
    assert result["failure_code"] == "application_health_check_failed"


def test_interrupted_deployment_journal_recovers_from_verified_snapshot(
    tmp_path: Path,
) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    deployer, _systemd, paths = _make_deployer(tmp_path, manager)
    snapshot = deployer._create_snapshot(staged.update_id)
    deployer._write_journal(staged.update_id, {
        "update_id": staged.update_id, "state": "applying",
        "manifest_hash": staged.release_hash, "snapshot": str(snapshot),
        "administrator_id": ADMIN_ID, "updated_at": "interrupted",
        "telegram_bot_id": 987654,
    })
    deployer._activate_release(deployer._prepare_versioned_release(
        staged.update_id, staged.release_hash,
        deployer._verify_staged_release(
            staged.update_id, staged.release_hash, staged.archive_sha256,
        ),
        staged.release_dir,
    ))

    deployer.recover_interrupted_deployments()

    assert deployer._active_release_path().name == "initial"
    assert (manager.project_root / "src/enan_ultra/worker.py").read_bytes() == b"VALUE = 'old'\n"
    status = json.loads((paths.history_root / f"{staged.update_id}.json").read_text())
    assert status["state"] == "rolled_back"


def test_preflight_rejects_different_systemd_service_layout(tmp_path: Path) -> None:
    manager, _staged = _make_release(tmp_path)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    systemd.show = lambda: {
        "User": "azureenan", "WorkingDirectory": "/home/azureenan/personal-telegram-ai",
        "ExecStart": "/home/azureenan/personal-telegram-ai/assistant.py",
        "ActiveState": "active", "MainPID": "100",
    }

    result = deployer.preflight()

    assert result["ready"] is False
    assert result["backup_status"] == "blocked"


def test_preflight_rejects_operator_selected_home_owned_package_service(
    tmp_path: Path,
) -> None:
    manager, _staged = _make_release(tmp_path)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)
    systemd.show = lambda: {
        "User": "azureenan",
        "WorkingDirectory": "/home/azureenan/Telegram-Bot-main",
        "ExecStart": (
            "/home/azureenan/Telegram-Bot-main/.venv/bin/python "
            "-m enan_ultra"
        ),
        "ActiveState": "active",
        "MainPID": "100",
    }

    result = deployer.preflight()

    assert result["ready"] is False
    assert result["backup_status"] == "blocked"
    assert result["rollback_status"] == "blocked"
    assert result["issues"] == ["deployment_prerequisite_failed"]
    assert systemd.restart_count == 0


def test_preflight_rejects_expected_interpreter_only_mentioned_as_argument(
    tmp_path: Path,
) -> None:
    manager, _staged = _make_release(tmp_path)
    deployer, systemd, paths = _make_deployer(tmp_path, manager)
    expected_python = str(paths.app_root / ".venv" / "bin" / "python")
    systemd.show = lambda: {
        "User": "enan-ultra",
        "Group": "enan-ultra",
        "WorkingDirectory": str(paths.current_release),
        "EnvironmentFiles": f"{paths.environment_file} (ignore_errors=no)",
        "ExecStart": (
            "{ path=/tmp/untrusted ; argv[]=/tmp/untrusted "
            f"{expected_python} -m enan_ultra }}"
        ),
        "ActiveState": "active",
        "MainPID": "100",
    }

    result = deployer.preflight()

    assert result["ready"] is False
    assert result["issues"] == ["deployment_prerequisite_failed"]
    assert systemd.restart_count == 0


def test_snapshot_capacity_includes_the_proposed_snapshot(tmp_path: Path, monkeypatch) -> None:
    manager, staged = _make_release(tmp_path)
    deployer, _systemd, paths = _make_deployer(tmp_path, manager)
    old_backup = paths.backup_root / "old"
    old_backup.mkdir()
    (old_backup / "payload.bin").write_bytes(b"x" * 128)
    monkeypatch.setattr(deployment_module, "MAX_BACKUP_STORAGE_BYTES", 1024)

    assert not deployer._backup_capacity_available(projected_snapshot_bytes=1024)


def test_rejects_archive_dependency_and_approval_control_changes(tmp_path: Path) -> None:
    manager, _staged = _make_release(
        tmp_path,
        additional_files={
            "pyproject.toml": "[project]\nname='changed'\n",
            "src/enan_ultra/handlers.py": "# altered approval logic\n",
        },
    )
    asyncio.run(manager.run_tests(
        manager.updates[next(iter(manager.updates))], _PassingSandbox(),
    ))
    staged = next(iter(manager.updates.values()))
    _approve_staging(manager, staged)
    deployer, _systemd, _paths = _make_deployer(tmp_path, manager)

    result = _deploy(deployer, staged)

    assert result["state"] == "failed"
    assert result["failure_code"] in {
        "dependency_change_requires_operator", "deployment_control_file_protected",
    }


def test_broker_rejects_case_collision_between_directory_and_implicit_parent(
    tmp_path: Path,
) -> None:
    manager, _staged = _make_release(tmp_path)
    deployer, _systemd, _paths = _make_deployer(tmp_path, manager)
    entries = [
        zipfile.ZipInfo("src/enan_ultra/Code/"),
        zipfile.ZipInfo("src/enan_ultra/code/extra.py"),
    ]

    with pytest.raises(DeploymentError, match="archive_path_case_collision"):
        deployer._validate_archive_path_collisions(entries)


def test_staging_state_path_places_database_and_health_marker_in_writable_root() -> None:
    project_root = Path(__file__).resolve().parents[1]
    unit = (project_root / "deploy" / "telegram-ai.service").read_text(encoding="utf-8")
    env_example = (project_root / ".env.example").read_text(encoding="utf-8")
    dropin = (
        project_root / "deploy" / "staging" / "telegram-ai.service.d"
        / "60-state-paths.conf"
    ).read_text(encoding="utf-8")
    state_env = (
        project_root / "deploy" / "staging" / "staging-state.env.example"
    ).read_text(encoding="utf-8")
    installer = (project_root / "deploy" / "install_ubuntu.sh").read_text(encoding="utf-8")

    env_files = re.findall(r"^EnvironmentFile=(.*)$", dropin, flags=re.MULTILINE)
    database_value = re.search(r"^DATABASE_PATH=(.+)$", state_env, flags=re.MULTILINE)
    default_database = re.search(r"^DATABASE_PATH=(.+)$", env_example, flags=re.MULTILINE)
    working_directory = re.search(r"^WorkingDirectory=(.+)$", unit, flags=re.MULTILINE)
    writable_paths = re.search(r"^ReadWritePaths=(.+)$", unit, flags=re.MULTILINE)
    assert env_files == ["", "/etc/telegram-ai/staging.env", "/etc/telegram-ai/staging-state.env"]
    assert database_value is not None
    assert default_database is not None
    assert working_directory is not None
    assert writable_paths is not None
    assert "DATABASE_PATH=/var/lib/telegram-ai/telegram_ai.sqlite3" in installer
    assert "DATABASE_PATH=data/telegram_ai.sqlite3" in installer

    writable_roots = {PurePosixPath(item) for item in writable_paths.group(1).split()}
    assert default_database.group(1) == "data/telegram_ai.sqlite3"
    installer_database_path = PurePosixPath("/var/lib/telegram-ai/telegram_ai.sqlite3")
    assert installer_database_path.parent in writable_roots
    assert (
        _deployment_health_marker_path(installer_database_path).as_posix()
        == deployment_module.HEALTH_MARKER.as_posix()
    )

    database_path = PurePosixPath(database_value.group(1))
    assert database_path.is_absolute()
    assert database_path.parent in writable_roots
    assert (
        _deployment_health_marker_path(database_path).as_posix()
        == deployment_module.HEALTH_MARKER.as_posix()
    )


def test_installer_keeps_deployment_socket_disabled_by_default() -> None:
    project_root = Path(__file__).resolve().parents[1]
    installer = (project_root / "deploy" / "install_ubuntu.sh").read_text(encoding="utf-8")

    assert not re.search(
        r"^\s*systemctl\s+(?:enable|start)\b[^\n]*telegram-ai-deploy\.socket",
        installer,
        flags=re.MULTILINE,
    )
    assert "A versioned release already exists" in installer
    assert "systemctl is-active --quiet telegram-ai.service" in installer
    assert installer.index("A versioned release already exists") < installer.index("apt-get update")


def test_sqlite_online_backup_api_captures_wal_and_restores_a_copy(tmp_path: Path) -> None:
    source_path = tmp_path / "staging.sqlite3"
    backup_path = tmp_path / "backups" / "staging.sqlite3"
    restore_path = tmp_path / "restored" / "staging.sqlite3"
    backup_path.parent.mkdir()
    restore_path.parent.mkdir()

    source = sqlite3.connect(source_path)
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    source.execute("INSERT INTO sample VALUES ('committed-in-wal')")
    source.commit()
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup, pages=1, sleep=0.01)
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        restored = sqlite3.connect(restore_path)
        try:
            backup.backup(restored, pages=1, sleep=0.01)
            assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert restored.execute("SELECT value FROM sample").fetchone() == (
                "committed-in-wal",
            )
        finally:
            restored.close()
    finally:
        backup.close()
        source.close()


def test_database_implementation_is_protected_from_zip_deployment(tmp_path: Path) -> None:
    manager, staged = _make_release(
        tmp_path,
        test_sandbox=_PassingSandbox(),
        additional_files={
            "src/enan_ultra/storage/database.py": "def migrate():\n    pass\n",
        },
    )
    _approve_staging(manager, staged)
    deployer, systemd, _paths = _make_deployer(tmp_path, manager)

    result = _deploy(deployer, staged)

    assert result["state"] == "failed"
    assert result["failure_code"] == "deployment_control_file_protected"
    assert systemd.restart_count == 0


def test_deployment_status_redacts_secrets(tmp_path: Path) -> None:
    manager, staged = _make_release(tmp_path, test_sandbox=_PassingSandbox())
    _approve_staging(manager, staged)
    deployer, _systemd, paths = _make_deployer(tmp_path, manager)
    deployer._write_status(staged.update_id, {
        "update_id": staged.update_id, "state": "failed",
        "failure_code": "TOKEN=unit-test-placeholder",
        "manifest_hash": staged.release_hash, "administrator_id": ADMIN_ID,
        "updated_at": "now",
    })

    result = deployer.status(staged.update_id)

    assert result is not None
    assert result["failure_code"] == "deployment_failed"
