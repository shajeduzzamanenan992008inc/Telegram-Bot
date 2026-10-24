#!/usr/bin/env python3
"""Root-owned, allowlisted deployment broker for the Telegram ZIP updater."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import socket
import socketserver
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SERVICE_NAME = "telegram-ai.service"
APP_ROOT = Path("/opt/enan-ultra")
CURRENT_RELEASE = APP_ROOT / "current"
RELEASES_ROOT = APP_ROOT / "releases"
BOT_STATE_ROOT = Path("/var/lib/telegram-ai")
DEPLOY_STATE_ROOT = Path("/var/lib/telegram-ai-deploy")
SOCKET_PATH = Path("/run/telegram-ai-deploy.sock")
ENABLE_FLAG = Path("/etc/telegram-ai/zip-deploy.enabled")
SERVICE_USER = "enan-ultra"
HEALTH_MARKER = BOT_STATE_ROOT / "runtime-health.json"
ENVIRONMENT_FILE = Path("/etc/telegram-ai/bot.env")
STAGING_ROOT = BOT_STATE_ROOT / ".telegram-zip-updates"
BACKUP_ROOT = DEPLOY_STATE_ROOT / "release-backups"
HISTORY_ROOT = DEPLOY_STATE_ROOT / "deployment-history"
LOCK_PATH = DEPLOY_STATE_ROOT / "deployment.lock"
AUTHORIZATION_DB = DEPLOY_STATE_ROOT / "deployment-authorizations.sqlite3"
ALLOWED_PREFIX = "src/enan_ultra/"
ALLOWED_SUFFIXES = frozenset({".py", ".json", ".html", ".css", ".js"})
BLOCKED_FILES = frozenset({
    "src/enan_ultra/config.py",
    "src/enan_ultra/handlers.py",
    "src/enan_ultra/runtime.py",
    "src/enan_ultra/security.py",
    "src/enan_ultra/storage/database.py",
    "src/enan_ultra/services/telegram_zip_updates.py",
})
BLOCKED_DEPENDENCY_FILES = frozenset({"pyproject.toml", "requirements.txt", "uv.lock"})
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_RELEASE_BYTES = 50 * 1024 * 1024
MAX_RELEASE_FILES = 1000
MAX_BACKUP_SNAPSHOTS = 5
MAX_BACKUP_STORAGE_BYTES = 500 * 1024 * 1024
REQUEST_LIMIT = 16 * 1024
HEALTH_TIMEOUT_SECONDS = 90
OPERATOR_APPROVAL_TTL_SECONDS = 15 * 60
MAX_PENDING_OPERATOR_APPROVALS = 20
MAX_AUTHORIZATION_RECORDS = 100_000
SYSTEMCTL = Path("/usr/bin/systemctl")
_UPDATE_ID = re.compile(r"^[0-9a-f]{8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVE_COMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_PATH_LENGTH = 240
MAX_ARCHIVE_DEPTH = 8
MAX_ARCHIVE_COMPRESSION_RATIO = 100


class DeploymentError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeploymentPaths:
    app_root: Path = APP_ROOT
    current_release: Path = CURRENT_RELEASE
    releases_root: Path = RELEASES_ROOT
    environment_file: Path = ENVIRONMENT_FILE
    state_root: Path = DEPLOY_STATE_ROOT
    staging_root: Path = STAGING_ROOT
    backup_root: Path = BACKUP_ROOT
    history_root: Path = HISTORY_ROOT
    authorization_db: Path = AUTHORIZATION_DB
    health_marker: Path = HEALTH_MARKER
    lock_path: Path = LOCK_PATH
    enable_flag: Path = ENABLE_FLAG


class Systemd:
    def __init__(self, runner: Any = None) -> None:
        self.runner = runner or self._run

    @staticmethod
    def _run(arguments: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments, capture_output=True, check=False, text=True, timeout=timeout,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )

    def show(self) -> dict[str, str]:
        try:
            result = self.runner([
                str(SYSTEMCTL), "show", SERVICE_NAME, "--no-pager",
                "--property=User", "--property=WorkingDirectory", "--property=ExecStart",
                "--property=Group", "--property=EnvironmentFiles",
                "--property=ActiveState", "--property=MainPID",
            ], 10)
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError("service_query_timeout") from exc
        if result.returncode != 0:
            raise DeploymentError("service_query_failed")
        return dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

    def restart(self) -> None:
        try:
            result = self.runner([str(SYSTEMCTL), "restart", SERVICE_NAME], 45)
        except subprocess.TimeoutExpired as exc:
            raise DeploymentError("service_restart_timeout") from exc
        if result.returncode != 0:
            raise DeploymentError("service_restart_failed")


class ReleaseDeployer:
    def __init__(
        self,
        paths: DeploymentPaths = DeploymentPaths(),
        *,
        systemd: Systemd | None = None,
        require_root: bool = True,
        health_timeout: int = HEALTH_TIMEOUT_SECONDS,
        sleep: Any = time.sleep,
        clock: Any = time.time,
    ) -> None:
        self.paths = paths
        self.systemd = systemd or Systemd()
        self.require_root = require_root
        self.health_timeout = health_timeout
        self.sleep = sleep
        self.clock = clock
        self._job_lock = threading.Lock()
        self._submission_lock = threading.Lock()
        self._status_lock = threading.RLock()
        self._activation_scheduled = False

    def request_deployment(
        self,
        update_id: str,
        manifest_hash: str,
        archive_hash: str,
        claimed_administrator_id: int,
    ) -> bool:
        # The Telegram service may submit a request, but its claimed admin ID is
        # informational only. Activation requires a separate root peer approval.
        self._validate_request(
            update_id, manifest_hash, archive_hash, claimed_administrator_id,
        )
        self._validate_state_paths()
        with self._submission_lock:
            connection = self._authorization_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = self._clock_now()
                connection.execute(
                    "UPDATE deployment_requests SET state='expired' "
                    "WHERE state='pending' AND expires_at<=?", (now,),
                )
                pending_count = connection.execute(
                    "SELECT COUNT(*) FROM deployment_requests WHERE state='pending'",
                ).fetchone()[0]
                if pending_count >= MAX_PENDING_OPERATOR_APPROVALS:
                    connection.rollback()
                    raise DeploymentError("operator_approval_queue_full")
                record_count = connection.execute(
                    "SELECT COUNT(*) FROM deployment_requests",
                ).fetchone()[0]
                if record_count >= MAX_AUTHORIZATION_RECORDS:
                    connection.rollback()
                    raise DeploymentError("authorization_record_capacity_reached")
                connection.execute(
                    "INSERT INTO deployment_requests "
                    "(update_id, operation, manifest_hash, archive_hash, claimed_administrator_id, "
                    "requested_at, expires_at, state) VALUES (?, 'deploy', ?, ?, ?, ?, ?, 'pending')",
                    (
                        update_id, manifest_hash, archive_hash,
                        claimed_administrator_id, now,
                        now + OPERATOR_APPROVAL_TTL_SECONDS,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            except (sqlite3.Error, OSError) as exc:
                connection.rollback()
                raise DeploymentError("authorization_state_unavailable") from exc
            finally:
                connection.close()

            self._write_status(update_id, {
                "update_id": update_id,
                "state": "awaiting_operator_authorization",
                "manifest_hash": manifest_hash,
                "claimed_administrator_id": claimed_administrator_id,
                "updated_at": self._now(), "notified": False,
            })
            self._append_audit(
                update_id, claimed_administrator_id,
                "awaiting_operator_authorization", manifest_hash,
            )
            return True

    def pending_operator_approval(self, update_id: str) -> dict[str, Any]:
        if not _UPDATE_ID.fullmatch(update_id):
            raise DeploymentError("invalid_update_id")
        connection = self._authorization_connection()
        try:
            row = connection.execute(
                "SELECT update_id, operation, manifest_hash, archive_hash, "
                "claimed_administrator_id, requested_at, expires_at, state "
                "FROM deployment_requests WHERE update_id=?",
                (update_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise DeploymentError("authorization_state_unavailable") from exc
        finally:
            connection.close()
        if not row or row[7] != "pending" or row[1] != "deploy":
            raise DeploymentError("operator_approval_missing_or_used")
        if self._clock_now() >= float(row[6]):
            self._expire_operator_request(update_id)
            raise DeploymentError("operator_approval_expired")
        return {
            "update_id": row[0],
            "operation": row[1],
            "manifest_hash": row[2],
            "archive_hash": row[3],
            "claimed_administrator_id": row[4],
            "requested_at": float(row[5]),
            "expires_at": float(row[6]),
        }

    def authorize_pending_deployment(
        self, update_id: str, manifest_hash: str, operator_uid: int,
    ) -> bool:
        if operator_uid != 0:
            raise DeploymentError("unauthorized_operator")
        if not _UPDATE_ID.fullmatch(update_id) or not _SHA256.fullmatch(manifest_hash):
            raise DeploymentError("invalid_operator_approval")
        if self._job_lock.locked():
            raise DeploymentError("deployment_already_running")

        with self._submission_lock:
            if self._activation_scheduled or self._job_lock.locked():
                raise DeploymentError("deployment_already_running")
            connection = self._authorization_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT manifest_hash, archive_hash, claimed_administrator_id, "
                    "expires_at, state, operation FROM deployment_requests WHERE update_id=?",
                    (update_id,),
                ).fetchone()
                if not row or row[4] != "pending" or row[5] != "deploy":
                    raise DeploymentError("operator_approval_missing_or_used")
                if manifest_hash != row[0]:
                    raise DeploymentError("operator_approval_digest_mismatch")
                now = self._clock_now()
                if now >= float(row[3]):
                    connection.execute(
                        "UPDATE deployment_requests SET state='expired' WHERE update_id=?",
                        (update_id,),
                    )
                    connection.commit()
                    raise DeploymentError("operator_approval_expired")
                connection.execute(
                    "UPDATE deployment_requests SET state='consumed', authorized_at=?, "
                    "authorized_by_uid=? WHERE update_id=? AND state='pending'",
                    (now, operator_uid, update_id),
                )
                if connection.total_changes != 1:
                    raise DeploymentError("operator_approval_missing_or_used")
                connection.commit()
            except DeploymentError:
                if connection.in_transaction:
                    connection.rollback()
                raise
            except (sqlite3.Error, OSError) as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise DeploymentError("authorization_state_unavailable") from exc
            finally:
                connection.close()

            archive_hash = str(row[1])
            administrator_id = int(row[2])
            self._write_status(update_id, {
                "update_id": update_id, "state": "queued",
                "manifest_hash": manifest_hash,
                "administrator_id": administrator_id,
                "authorized_by_uid": operator_uid,
                "updated_at": self._now(), "notified": False,
            })
            self._append_audit(
                update_id, administrator_id, "authorized_by_root_operator",
                manifest_hash, operator_uid=operator_uid,
            )
            worker = threading.Thread(
                target=self._run_submitted,
                args=(update_id, manifest_hash, archive_hash, administrator_id),
                daemon=True,
                name=f"deploy-{update_id}",
            )
            try:
                self._activation_scheduled = True
                worker.start()
            except RuntimeError as exc:
                self._activation_scheduled = False
                self._write_status(update_id, {
                    "update_id": update_id, "state": "failed",
                    "manifest_hash": manifest_hash,
                    "administrator_id": administrator_id,
                    "authorized_by_uid": operator_uid,
                    "updated_at": self._now(),
                    "failure_code": "deployment_worker_unavailable",
                    "notified": False,
                })
                raise DeploymentError("deployment_worker_unavailable") from exc
            return True

    def recover_interrupted_deployments(self) -> None:
        if not self.paths.history_root.is_dir():
            return
        for status_path in self.paths.history_root.glob("????????.json"):
            if status_path.is_symlink():
                continue
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("state") in {"queued", "validating", "snapshot_created"}:
                update_id = str(status.get("update_id", ""))
                if not _UPDATE_ID.fullmatch(update_id):
                    continue
                status.update({
                    "state": "failed", "updated_at": self._now(),
                    "failure_code": "deployment_interrupted_before_activation",
                    "notified": False,
                })
                self._write_status(update_id, status)
                self._append_audit(
                    update_id, int(status.get("administrator_id", 0)),
                    "failed", str(status.get("manifest_hash", "")),
                )
                continue
            if status.get("state") not in {"applying", "restarting"}:
                continue
            update_id = str(status.get("update_id", ""))
            snapshot_value = status.get("snapshot")
            if not _UPDATE_ID.fullmatch(update_id) or not isinstance(snapshot_value, str):
                continue
            snapshot = Path(snapshot_value)
            try:
                self._restore_snapshot(snapshot)
                old_pid = int(self.systemd.show().get("MainPID", "0") or 0)
                self.systemd.restart()
                recovered_pid = self._wait_healthy(
                    old_pid, int(status["telegram_bot_id"]),
                    self.clock() + self.health_timeout,
                )
                status.update({
                    "state": "rolled_back", "updated_at": self._now(),
                    "failure_code": "interrupted_deployment_recovered",
                    "active_pid": recovered_pid, "notified": False,
                })
            except (DeploymentError, OSError, ValueError):
                status.update({
                    "state": "rollback_failed", "updated_at": self._now(),
                    "failure_code": "interrupted_deployment_recovery_failed",
                    "notified": False,
                })
            self._write_status(update_id, status)
            self._append_audit(
                update_id, int(status.get("administrator_id", 0)),
                str(status["state"]), str(status.get("manifest_hash", "")),
            )

    def _run_submitted(
        self, update_id: str, manifest_hash: str, archive_hash: str, administrator_id: int,
    ) -> None:
        try:
            self.deploy(update_id, manifest_hash, archive_hash, administrator_id)
        except Exception as exc:
            traceback = exc.__traceback__
            while traceback is not None and traceback.tb_next is not None:
                traceback = traceback.tb_next
            location = (
                traceback.tb_frame.f_code.co_name.lower() if traceback is not None else "unknown"
            )
            failure_code = (
                exc.code if isinstance(exc, DeploymentError)
                else f"unexpected_{type(exc).__name__.lower()}_in_{location}"
            )
            if not re.fullmatch(r"[a-z0-9_]{1,64}", failure_code):
                failure_code = "deployment_failed"
            self._write_status(update_id, {
                **(self.status(update_id) or {}),
                "update_id": update_id, "state": "failed",
                "manifest_hash": manifest_hash, "administrator_id": administrator_id,
                "updated_at": self._now(), "failure_code": failure_code,
                "notified": False,
            })
        finally:
            with self._submission_lock:
                self._activation_scheduled = False

    def preflight(self) -> dict[str, Any]:
        issues: list[str] = []
        try:
            self._check_environment()
            self._check_baseline_health()
            snapshot_bytes = self._snapshot_bytes_required()
            required_state_space = (
                snapshot_bytes + MAX_RELEASE_BYTES * 2
                + MAX_ARCHIVE_COMPRESSED_BYTES + MAX_MANIFEST_BYTES
            )
            if shutil.disk_usage(self.paths.state_root).free < required_state_space:
                issues.append("insufficient_backup_space")
            if not self._release_capacity_available(snapshot_bytes):
                issues.append("insufficient_release_space")
            if not self._backup_capacity_available(snapshot_bytes):
                issues.append("backup_retention_limit_reached")
        except (DeploymentError, OSError):
            issues.append("deployment_prerequisite_failed")
        ready = not issues
        return {
            "ready": ready,
            "issues": issues,
            "backup_status": "snapshot_available" if ready else "blocked",
            "rollback_status": "snapshot_on_deploy" if ready else "blocked",
            "service": SERVICE_NAME,
        }

    def deploy(
        self,
        update_id: str,
        manifest_hash: str,
        archive_hash: str,
        administrator_id: int,
    ) -> dict[str, Any]:
        self._validate_request(update_id, manifest_hash, archive_hash, administrator_id)
        if not self._job_lock.acquire(blocking=False):
            raise DeploymentError("deployment_already_running")
        lock_handle: Any = None
        try:
            self._validate_state_paths()
            lock_handle = self._acquire_os_lock()
            return self._deploy_locked(
                update_id, manifest_hash, archive_hash, administrator_id,
            )
        finally:
            if lock_handle is not None:
                self._release_os_lock(lock_handle)
            self._job_lock.release()

    def _deploy_locked(
        self,
        update_id: str,
        manifest_hash: str,
        archive_hash: str,
        administrator_id: int,
    ) -> dict[str, Any]:
        self._write_status(update_id, {
            "update_id": update_id, "state": "validating", "manifest_hash": manifest_hash,
            "administrator_id": administrator_id, "updated_at": self._now(),
            "notified": False,
        })
        snapshot: Path | None = None
        activated = False
        try:
            self._check_environment()
            telegram_bot_id = self._check_baseline_health()
            snapshot_bytes = self._snapshot_bytes_required()
            if shutil.disk_usage(self.paths.state_root).free < (
                snapshot_bytes + MAX_RELEASE_BYTES * 2
                + MAX_ARCHIVE_COMPRESSED_BYTES + MAX_MANIFEST_BYTES
            ):
                raise DeploymentError("insufficient_backup_space")
            if not self._release_capacity_available(snapshot_bytes):
                raise DeploymentError("insufficient_release_space")
            if not self._backup_capacity_available(snapshot_bytes):
                raise DeploymentError("backup_retention_limit_reached")
            with tempfile.TemporaryDirectory(
                prefix="telegram-deploy-", dir=self.paths.state_root,
            ) as private_dir:
                private_update = Path(private_dir) / update_id
                private_update.mkdir(mode=0o700)
                self._copy_staged_release(
                    update_id, private_update, manifest_hash, archive_hash,
                )
                manifest = self._verify_staged_release(
                    update_id, manifest_hash, archive_hash, update_root=private_update,
                )
                snapshot = self._create_snapshot(update_id)
                self._write_journal(update_id, {
                    "update_id": update_id, "state": "snapshot_created",
                    "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                    "administrator_id": administrator_id, "updated_at": self._now(),
                    "telegram_bot_id": telegram_bot_id,
                })
                old_pid = int(self.systemd.show().get("MainPID", "0") or 0)
                next_release = self._prepare_versioned_release(
                    update_id, manifest_hash, manifest,
                    private_update / "release",
                )
                activated = True
                self._write_journal(update_id, {
                    "update_id": update_id, "state": "applying",
                    "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                    "administrator_id": administrator_id, "updated_at": self._now(),
                    "telegram_bot_id": telegram_bot_id,
                    "release": next_release.name,
                })
                self._activate_release(next_release)
                self._write_journal(update_id, {
                    "update_id": update_id, "state": "restarting",
                    "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                    "administrator_id": administrator_id, "updated_at": self._now(),
                    "telegram_bot_id": telegram_bot_id,
                    "release": next_release.name,
                })
                self.systemd.restart()
                new_pid = self._wait_healthy(
                    old_pid, telegram_bot_id, self.clock() + self.health_timeout,
                )
                self._write_journal(update_id, {
                    "update_id": update_id, "state": "succeeded",
                    "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                    "administrator_id": administrator_id, "updated_at": self._now(),
                    "active_pid": new_pid,
                    "release": next_release.name,
                })
                self._append_audit(update_id, administrator_id, "succeeded", manifest_hash)
                return self._public_status(update_id)
        except (DeploymentError, OSError, ValueError, zipfile.BadZipFile) as exc:
            code = exc.code if isinstance(exc, DeploymentError) else "deployment_failed"
            if activated and snapshot is not None:
                try:
                    self._restore_snapshot(snapshot)
                    old_pid = int(self.systemd.show().get("MainPID", "0") or 0)
                    self.systemd.restart()
                    recovered_pid = self._wait_healthy(
                        old_pid, telegram_bot_id, self.clock() + self.health_timeout,
                    )
                    state = "rolled_back"
                    self._write_journal(update_id, {
                        "update_id": update_id, "state": state,
                        "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                        "administrator_id": administrator_id, "updated_at": self._now(),
                        "failure_code": code, "active_pid": recovered_pid,
                    })
                    self._append_audit(update_id, administrator_id, state, manifest_hash)
                except (DeploymentError, OSError, ValueError):
                    state = "rollback_failed"
                    self._write_journal(update_id, {
                        "update_id": update_id, "state": state,
                        "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                        "administrator_id": administrator_id, "updated_at": self._now(),
                        "failure_code": code,
                    })
                    self._append_audit(update_id, administrator_id, state, manifest_hash)
            else:
                state = "failed"
                self._write_journal(update_id, {
                    "update_id": update_id, "state": state,
                    "manifest_hash": manifest_hash, "administrator_id": administrator_id,
                    "updated_at": self._now(), "failure_code": code,
                })
                self._append_audit(update_id, administrator_id, state, manifest_hash)
            return self._public_status(update_id)

    def _check_environment(self) -> None:
        if self.require_root and os.geteuid() != 0:
            raise DeploymentError("helper_not_root")
        self._validate_state_paths()
        if not self.paths.enable_flag.is_file() or self.paths.enable_flag.is_symlink():
            raise DeploymentError("deployment_not_operator_enabled")
        if self.require_root and (
            self.paths.enable_flag.stat().st_uid != 0
            or stat.S_IMODE(self.paths.enable_flag.stat().st_mode) & 0o022
        ):
            raise DeploymentError("deployment_enable_flag_unsafe")
        if (
            self.paths.app_root.is_symlink() or not self.paths.app_root.is_dir()
            or self.paths.current_release != self.paths.app_root / "current"
            or self.paths.releases_root != self.paths.app_root / "releases"
            or self.paths.releases_root.is_symlink() or not self.paths.releases_root.is_dir()
        ):
            raise DeploymentError("application_path_invalid")
        if self.require_root:
            app_stat = self.paths.app_root.stat()
            release_stat = self.paths.releases_root.stat()
            if (
                app_stat.st_uid != 0 or stat.S_IMODE(app_stat.st_mode) & 0o022
                or release_stat.st_uid != 0 or stat.S_IMODE(release_stat.st_mode) & 0o022
            ):
                raise DeploymentError("application_root_permissions_invalid")
            if (
                self.paths.environment_file.is_symlink()
                or not self.paths.environment_file.is_file()
                or self.paths.environment_file.stat().st_uid != 0
                or stat.S_IMODE(self.paths.environment_file.stat().st_mode) & 0o077
            ):
                raise DeploymentError("environment_file_permissions_invalid")
        active_release = self._active_release_path()
        self._validate_active_release_layout(active_release)
        if self.require_root:
            for path in (
                active_release, active_release / "src",
                active_release / "src" / "enan_ultra",
            ):
                active_stat = path.stat()
                if active_stat.st_uid != 0 or stat.S_IMODE(active_stat.st_mode) & 0o022:
                    raise DeploymentError("active_release_permissions_invalid")
            if stat.S_IMODE(active_release.stat().st_mode) & 0o222:
                raise DeploymentError("active_release_permissions_invalid")
        details = self.systemd.show()
        exec_start = details.get("ExecStart", "")
        expected_python = str(self.paths.app_root / ".venv" / "bin" / "python")
        if (
            details.get("User") != SERVICE_USER
            or details.get("Group") != SERVICE_USER
            or details.get("WorkingDirectory") != str(self.paths.current_release)
            or details.get("EnvironmentFiles", "").split(" ", 1)[0]
            != str(self.paths.environment_file)
            or not self._service_entrypoint_matches(exec_start, expected_python)
        ):
            raise DeploymentError("service_layout_mismatch")

    def _active_release_path(self) -> Path:
        """Resolve the root-controlled current pointer without accepting arbitrary paths."""
        if os.name == "nt" and not self.require_root:
            pointer = self.paths.app_root / ".current-release"
            if pointer.is_symlink() or not pointer.is_file():
                raise DeploymentError("current_release_pointer_invalid")
            target = pointer.read_text(encoding="utf-8").strip()
        else:
            link = self.paths.current_release
            if not link.is_symlink():
                raise DeploymentError("current_release_pointer_invalid")
            target = os.readlink(link)
        pure_target = PurePosixPath(target)
        if (
            pure_target.is_absolute() or len(pure_target.parts) != 2
            or pure_target.parts[0] != "releases"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", pure_target.parts[1])
            or "\\" in target or ":" in target
        ):
            raise DeploymentError("current_release_target_invalid")
        releases_root = self.paths.releases_root.resolve(strict=True)
        unresolved_release = self.paths.app_root / Path(*pure_target.parts)
        if unresolved_release.is_symlink():
            raise DeploymentError("current_release_target_invalid")
        try:
            release = unresolved_release.resolve(strict=True)
        except OSError as exc:
            raise DeploymentError("active_release_missing") from exc
        if (
            release.parent != releases_root or release.is_symlink()
            or not release.is_dir()
        ):
            raise DeploymentError("current_release_target_invalid")
        return release

    @staticmethod
    def _validate_active_release_layout(release: Path) -> None:
        source_root = release / "src"
        package_root = source_root / "enan_ultra"
        if (
            source_root.is_symlink() or not source_root.is_dir()
            or package_root.is_symlink() or not package_root.is_dir()
        ):
            raise DeploymentError("application_package_missing")
        try:
            root_entries = list(release.iterdir())
            source_entries = list(source_root.iterdir())
        except OSError as exc:
            raise DeploymentError("application_release_unreadable") from exc
        if (
            len(root_entries) != 1 or root_entries[0].name != "src"
            or len(source_entries) != 1 or source_entries[0].name != "enan_ultra"
        ):
            raise DeploymentError("application_release_layout_invalid")

    @staticmethod
    def _service_entrypoint_matches(exec_start: str, expected_python: str) -> bool:
        match = re.search(r"\bpath=([^;]+);\s*argv\[\]=([^;}]+)", exec_start)
        if not match:
            return False
        executable = match.group(1).strip()
        arguments = match.group(2).strip().split()
        return (
            executable == expected_python
            and arguments == [expected_python, "-m", "enan_ultra"]
        )

    def _validate_state_paths(self) -> None:
        for path in (self.paths.state_root, self.paths.backup_root, self.paths.history_root):
            if path.is_symlink() or not path.is_dir():
                raise DeploymentError("deployment_state_path_invalid")
            if self.require_root:
                details = path.stat()
                if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o077:
                    raise DeploymentError("deployment_state_permissions_invalid")

    def _authorization_connection(self) -> sqlite3.Connection:
        self._validate_state_paths()
        path = self.paths.authorization_db
        if path.parent != self.paths.state_root or path.is_symlink():
            raise DeploymentError("authorization_state_path_invalid")
        if path.exists() and self.require_root:
            details = path.stat()
            if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o077:
                raise DeploymentError("authorization_state_permissions_invalid")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS deployment_requests ("
                "update_id TEXT PRIMARY KEY, "
                "operation TEXT NOT NULL CHECK(operation='deploy'), "
                "manifest_hash TEXT NOT NULL, "
                "archive_hash TEXT NOT NULL, "
                "claimed_administrator_id INTEGER NOT NULL, "
                "requested_at REAL NOT NULL, "
                "expires_at REAL NOT NULL, "
                "state TEXT NOT NULL CHECK(state IN ('pending','consumed','expired')), "
                "authorized_at REAL, "
                "authorized_by_uid INTEGER)"
            )
            if os.name != "nt":
                path.chmod(0o600)
            return connection
        except (sqlite3.Error, OSError) as exc:
            if connection is not None:
                connection.close()
            raise DeploymentError("authorization_state_unavailable") from exc

    def _clock_now(self) -> float:
        try:
            now = float(self.clock())
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise DeploymentError("authorization_clock_unavailable") from exc
        if not math.isfinite(now) or now <= 0:
            raise DeploymentError("authorization_clock_unavailable")
        return now

    def _expire_operator_request(self, update_id: str) -> None:
        connection = self._authorization_connection()
        details: tuple[str, int] | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            details = connection.execute(
                "SELECT manifest_hash, claimed_administrator_id "
                "FROM deployment_requests WHERE update_id=? AND state='pending'",
                (update_id,),
            ).fetchone()
            connection.execute(
                "UPDATE deployment_requests SET state='expired' "
                "WHERE update_id=? AND state='pending' AND expires_at<=?",
                (update_id, self._clock_now()),
            )
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            if connection.in_transaction:
                connection.rollback()
            raise DeploymentError("authorization_state_unavailable") from exc
        finally:
            connection.close()
        current = self.status(update_id)
        if details and current and current.get("state") == "awaiting_operator_authorization":
            self._write_status(update_id, {
                **current,
                "state": "failed",
                "failure_code": "operator_approval_expired",
                "updated_at": self._now(),
                "notified": False,
            })
            self._append_audit(
                update_id, int(details[1]), "operator_approval_expired", details[0],
            )

    def _check_baseline_health(self) -> int:
        details = self.systemd.show()
        if details.get("ActiveState") != "active" or details.get("MainPID", "0") == "0":
            raise DeploymentError("service_not_active")
        try:
            marker = json.loads(self.paths.health_marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError("baseline_health_missing") from exc
        expected_import = (
            self._active_release_path() / "src" / "enan_ultra" / "runtime.py"
        ).resolve(strict=True)
        if (
            marker.get("pid") != int(details["MainPID"])
            or marker.get("database_open") is not True
            or not isinstance(marker.get("telegram_bot_id"), int)
            or float(marker.get("started_at", 0)) <= 0
            or marker.get("import_origin") != str(expected_import)
        ):
            raise DeploymentError("baseline_health_invalid")
        return int(marker["telegram_bot_id"])

    @staticmethod
    def _validate_request(
        update_id: str, manifest_hash: str, archive_hash: str,
        claimed_administrator_id: int,
    ) -> None:
        if (
            not _UPDATE_ID.fullmatch(update_id)
            or not _SHA256.fullmatch(manifest_hash)
            or not _SHA256.fullmatch(archive_hash)
            or isinstance(claimed_administrator_id, bool)
            or not isinstance(claimed_administrator_id, int)
            or claimed_administrator_id <= 0
        ):
            raise DeploymentError("invalid_deployment_request")

    @staticmethod
    def _validate_archive_path_collisions(infos: list[zipfile.ZipInfo]) -> None:
        file_path_keys = {
            info.filename.casefold() for info in infos if not info.is_dir()
        }
        seen_names: set[str] = set()
        directory_spellings: dict[str, str] = {}
        for info in infos:
            raw_name = info.filename.rstrip("/") if info.is_dir() else info.filename
            folded = raw_name.casefold()
            if folded in seen_names:
                raise DeploymentError("archive_duplicate_path")
            seen_names.add(folded)
            parts = PurePosixPath(raw_name).parts
            directory_parts = parts if info.is_dir() else parts[:-1]
            prefix = ""
            for component in directory_parts:
                prefix = f"{prefix}/{component}" if prefix else component
                directory_key = prefix.casefold()
                previous = directory_spellings.get(directory_key)
                if previous is not None and previous != prefix:
                    raise DeploymentError("archive_path_case_collision")
                directory_spellings[directory_key] = prefix
                if directory_key in file_path_keys:
                    raise DeploymentError("archive_file_parent_collision")

    def _verify_staged_release(
        self, update_id: str, manifest_hash: str, archive_hash: str,
        *, update_root: Path | None = None,
    ) -> dict[str, Any]:
        update_root = update_root or self.paths.staging_root / update_id
        release_root = update_root / "release"
        for path in (self.paths.staging_root, update_root, release_root):
            if path.is_symlink() or not path.is_dir():
                raise DeploymentError("staged_release_missing")
        manifest_path = update_root / "manifest.json"
        archive_path = update_root / "upload.zip"
        if manifest_path.is_symlink() or archive_path.is_symlink():
            raise DeploymentError("staged_release_unsafe")
        manifest_bytes = manifest_path.read_bytes()
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise DeploymentError("manifest_too_large")
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_hash:
            raise DeploymentError("manifest_hash_mismatch")
        if _hash_file(archive_path) != archive_hash:
            raise DeploymentError("archive_hash_mismatch")
        if archive_path.stat().st_size > MAX_ARCHIVE_COMPRESSED_BYTES:
            raise DeploymentError("archive_compressed_size_limit")
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict):
            raise DeploymentError("manifest_invalid")
        if (
            manifest.get("schema") != 2
            or manifest.get("update_id") != update_id
            or manifest.get("archive_sha256") != archive_hash
            or not isinstance(manifest.get("source_archive_sha256"), str)
            or not _SHA256.fullmatch(manifest["source_archive_sha256"])
            or manifest.get("validation") != {"syntax": "PASS", "tests": "PASS"}
        ):
            raise DeploymentError("mandatory_validation_not_passed")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) > MAX_RELEASE_FILES:
            raise DeploymentError("manifest_file_limit")
        expected: set[str] = set()
        total_size = 0
        source_manifest: dict[str, dict[str, Any]] = {}
        for item in files:
            if not isinstance(item, dict):
                raise DeploymentError("manifest_invalid")
            path = item.get("path")
            action = item.get("action")
            size = item.get("size")
            digest = item.get("sha256")
            old_digest = item.get("previous_sha256")
            if not isinstance(path, str) or not self._safe_manifest_path(path):
                raise DeploymentError("manifest_path_rejected")
            if path in expected:
                raise DeploymentError("manifest_duplicate_path")
            expected.add(path)
            if action not in {"added", "changed", "unchanged", "removed"}:
                raise DeploymentError("manifest_action_invalid")
            if (
                not isinstance(size, int) or size < 0
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                or (old_digest is not None and (
                    not isinstance(old_digest, str) or not _SHA256.fullmatch(old_digest)
                ))
                or (action == "added" and old_digest is not None)
                or (action != "added" and old_digest is None)
            ):
                raise DeploymentError("manifest_hash_invalid")
            total_size += size
            if total_size > MAX_RELEASE_BYTES:
                raise DeploymentError("release_size_limit")
            staged_file = release_root.joinpath(*PurePosixPath(path).parts)
            if action != "removed":
                if staged_file.is_symlink() or not staged_file.is_file():
                    raise DeploymentError("staged_file_missing")
                if staged_file.stat().st_size != size or _hash_file(staged_file) != digest:
                    raise DeploymentError("staged_file_hash_mismatch")
            if path.startswith(ALLOWED_PREFIX):
                if path in BLOCKED_FILES and action != "unchanged":
                    raise DeploymentError("deployment_control_file_protected")
                if action != "removed":
                    source_manifest[path] = item
            elif path in BLOCKED_DEPENDENCY_FILES and action != "unchanged":
                raise DeploymentError("dependency_change_requires_operator")
        actual: set[str] = set()
        for current in release_root.rglob("*"):
            if current.is_symlink():
                raise DeploymentError("staged_symlink_rejected")
            if current.is_file():
                actual.add(current.relative_to(release_root).as_posix())
        if actual != {
            item["path"] for item in files if item["action"] != "removed"
        }:
            raise DeploymentError("staged_file_list_mismatch")
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_RELEASE_FILES:
                raise DeploymentError("archive_file_limit")
            zip_names = {
                info.filename.rstrip("/") for info in infos if not info.is_dir()
            }
            if zip_names != {
                item["path"] for item in files if item["action"] != "removed"
            }:
                raise DeploymentError("archive_manifest_mismatch")
            self._validate_archive_path_collisions(infos)
            archived_files: dict[str, zipfile.ZipInfo] = {}
            expanded_size = 0
            for info in infos:
                raw_name = info.filename.rstrip("/") if info.is_dir() else info.filename
                if not self._safe_manifest_path(raw_name, directory=info.is_dir()):
                    raise DeploymentError("archive_path_rejected")
                if (
                    len(raw_name) > MAX_ARCHIVE_PATH_LENGTH
                    or len(PurePosixPath(raw_name).parts) - 1 > MAX_ARCHIVE_DEPTH
                ):
                    raise DeploymentError("archive_path_limit")
                mode = (info.external_attr >> 16) & 0o170000
                expected_mode = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
                if mode not in {0, expected_mode}:
                    raise DeploymentError("archive_special_file_rejected")
                if info.flag_bits & (0x1 | 0x40):
                    raise DeploymentError("archive_encryption_rejected")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise DeploymentError("archive_compression_rejected")
                if (
                    info.file_size < 0 or info.compress_size < 0
                    or (info.is_dir() and info.file_size)
                ):
                    raise DeploymentError("archive_size_metadata_invalid")
                expanded_size += info.file_size
                if expanded_size > MAX_RELEASE_BYTES:
                    raise DeploymentError("release_size_limit")
                if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_ARCHIVE_COMPRESSION_RATIO:
                    raise DeploymentError("archive_compression_ratio_limit")
                if info.is_dir():
                    continue
                path = info.filename
                if path not in expected:
                    raise DeploymentError("archive_manifest_mismatch")
                archived_files[path] = info
            by_path = {item["path"]: item for item in files if item["action"] != "removed"}
            for path, info in archived_files.items():
                digest = hashlib.sha256()
                try:
                    with archive.open(info) as source:
                        while chunk := source.read(64 * 1024):
                            digest.update(chunk)
                except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                    raise DeploymentError("archive_entry_unreadable") from exc
                if (
                    digest.hexdigest() != by_path[path]["sha256"]
                    or info.file_size != by_path[path]["size"]
                ):
                    raise DeploymentError("archive_entry_hash_mismatch")
        current = self._enumerate_app_files()
        expected_base = {
            item["path"]: item["previous_sha256"]
            for item in files if item["path"].startswith(ALLOWED_PREFIX)
            and item["action"] != "added"
        }
        if set(current) != set(expected_base):
            raise DeploymentError("live_release_file_list_changed")
        for path, old_digest in expected_base.items():
            if current[path] != old_digest:
                raise DeploymentError("live_release_changed_since_review")
        for item in files:
            path = item["path"]
            if not path.startswith(ALLOWED_PREFIX):
                continue
            staged_file = release_root.joinpath(*PurePosixPath(path).parts)
            if path in source_manifest and staged_file.suffix == ".py":
                try:
                    compile(staged_file.read_bytes(), path, "exec", dont_inherit=True)
                except (SyntaxError, UnicodeError) as exc:
                    raise DeploymentError("python_syntax_failed_at_deploy") from exc
        if not source_manifest:
            raise DeploymentError("no_deployable_application_files")
        return manifest

    def _copy_staged_release(
        self,
        update_id: str,
        private_update: Path,
        manifest_hash: str,
        archive_hash: str,
    ) -> None:
        manifest_bytes = self._read_staged_file(
            update_id, ("manifest.json",), MAX_MANIFEST_BYTES,
        )
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest_hash:
            raise DeploymentError("manifest_hash_mismatch")
        archive_bytes = self._read_staged_file(
            update_id, ("upload.zip",), MAX_ARCHIVE_COMPRESSED_BYTES,
        )
        if hashlib.sha256(archive_bytes).hexdigest() != archive_hash:
            raise DeploymentError("archive_hash_mismatch")
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict):
            raise DeploymentError("manifest_invalid")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) > MAX_RELEASE_FILES:
            raise DeploymentError("manifest_file_limit")
        release_root = private_update / "release"
        release_root.mkdir(mode=0o700)
        seen: set[str] = set()
        total_size = 0
        for item in files:
            if not isinstance(item, dict):
                raise DeploymentError("manifest_invalid")
            path = item.get("path")
            action = item.get("action")
            size = item.get("size")
            if (
                not isinstance(path, str) or not self._safe_manifest_path(path)
                or path in seen or action not in {"added", "changed", "unchanged", "removed"}
                or not isinstance(size, int) or size < 0
            ):
                raise DeploymentError("manifest_invalid")
            seen.add(path)
            total_size += size
            if total_size > MAX_RELEASE_BYTES:
                raise DeploymentError("release_size_limit")
            if action == "removed":
                continue
            content = self._read_staged_file(
                update_id, ("release", *PurePosixPath(path).parts), MAX_RELEASE_BYTES,
            )
            target = release_root.joinpath(*PurePosixPath(path).parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o600)
        (private_update / "manifest.json").write_bytes(manifest_bytes)
        (private_update / "manifest.json").chmod(0o600)
        (private_update / "upload.zip").write_bytes(archive_bytes)
        (private_update / "upload.zip").chmod(0o600)

    def _read_staged_file(
        self, update_id: str, relative_parts: tuple[str, ...], max_bytes: int,
    ) -> bytes:
        if (
            not _UPDATE_ID.fullmatch(update_id)
            or any(part in {"", ".", ".."} or "/" in part or "\\" in part
                   for part in relative_parts)
        ):
            raise DeploymentError("staged_path_rejected")
        if os.name == "nt":
            path = self.paths.staging_root / update_id
            for part in relative_parts:
                path /= part
                if path.is_symlink():
                    raise DeploymentError("staged_release_unsafe")
            try:
                if not path.is_file() or path.stat().st_size > max_bytes:
                    raise DeploymentError("staged_file_invalid")
                return path.read_bytes()
            except OSError as exc:
                raise DeploymentError("staged_file_unavailable") from exc

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        directory_fd = os.open("/", directory_flags)
        opened_fds = [directory_fd]
        try:
            for component in self.paths.staging_root.parts[1:]:
                directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                opened_fds.append(directory_fd)
            self._validate_staging_owner(os.fstat(directory_fd), directory=True)
            directory_fd = os.open(update_id, directory_flags, dir_fd=directory_fd)
            opened_fds.append(directory_fd)
            self._validate_staging_owner(os.fstat(directory_fd), directory=True)
            for component in relative_parts[:-1]:
                directory_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                opened_fds.append(directory_fd)
                self._validate_staging_owner(os.fstat(directory_fd), directory=True)
            file_fd = os.open(relative_parts[-1], file_flags, dir_fd=directory_fd)
            opened_fds.append(file_fd)
            file_stat = os.fstat(file_fd)
            self._validate_staging_owner(file_stat, directory=False)
            if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > max_bytes:
                raise DeploymentError("staged_file_invalid")
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(file_fd, min(64 * 1024, max_bytes + 1 - total)):
                total += len(chunk)
                if total > max_bytes:
                    raise DeploymentError("staged_file_invalid")
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError as exc:
            raise DeploymentError("staged_file_unavailable") from exc
        finally:
            for descriptor in reversed(opened_fds):
                os.close(descriptor)

    def _validate_staging_owner(self, file_stat: os.stat_result, *, directory: bool) -> None:
        if not self.require_root:
            return
        import pwd

        expected_uid = pwd.getpwnam(SERVICE_USER).pw_uid
        forbidden_mode = 0o077 if directory else 0o077
        if file_stat.st_uid != expected_uid or stat.S_IMODE(file_stat.st_mode) & forbidden_mode:
            raise DeploymentError("staged_path_permissions_invalid")

    @staticmethod
    def _safe_manifest_path(path: str, *, directory: bool = False) -> bool:
        pure = PurePosixPath(path)
        parts = pure.parts
        if (
            pure.is_absolute() or not parts or "\\" in path or ":" in path
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(char) < 32 for char in path)
        ):
            return False
        if directory:
            return path in {"src", "src/enan_ultra", "tests", "docs"} or path.startswith(
                ("src/enan_ultra/", "tests/", "docs/"),
            )
        if path.startswith(ALLOWED_PREFIX):
            return (
                len(parts) >= 3
                and pure.suffix.lower() in ALLOWED_SUFFIXES
                and "__pycache__" not in parts
            )
        if path in BLOCKED_DEPENDENCY_FILES:
            return True
        if path.startswith(("tests/", "docs/")):
            return True
        return path in {".env.example", "README.md", "CHANGELOG.md"}

    def _enumerate_app_files(self) -> dict[str, str]:
        return self._enumerate_release_files(self._active_release_path())

    def _enumerate_release_files(self, release_root: Path) -> dict[str, str]:
        package_root = release_root / "src" / "enan_ultra"
        if package_root.is_symlink() or not package_root.is_dir():
            raise DeploymentError("application_package_missing")
        result: dict[str, str] = {}
        for current in package_root.rglob("*"):
            if "__pycache__" in current.parts:
                continue
            if current.is_symlink():
                raise DeploymentError("application_release_symlink_rejected")
            if current.is_dir():
                if self.require_root and (
                    current.stat().st_uid != 0
                    or stat.S_IMODE(current.stat().st_mode) & 0o022
                ):
                    raise DeploymentError("application_release_permissions_invalid")
                if current.name == "__pycache__":
                    continue
                continue
            if not current.is_file():
                raise DeploymentError("application_release_special_file")
            if current.suffix.lower() not in ALLOWED_SUFFIXES:
                if current.suffix.lower() in {".pyc", ".pyo"}:
                    continue
                raise DeploymentError("application_release_unexpected_file")
            if self.require_root and (
                current.stat().st_uid != 0 or stat.S_IMODE(current.stat().st_mode) & 0o022
            ):
                raise DeploymentError("application_release_permissions_invalid")
            relative = current.relative_to(release_root).as_posix()
            result[relative] = _hash_file(current)
        return result

    def _create_snapshot(self, update_id: str) -> Path:
        stamp = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = self.paths.backup_root / f"{stamp}-{update_id}"
        if destination.exists() or destination.is_symlink():
            raise DeploymentError("backup_destination_exists")
        active_release = self._active_release_path()
        files = self._enumerate_app_files()
        required_bytes = self._snapshot_bytes_required(files)
        if not self._backup_capacity_available(required_bytes):
            raise DeploymentError("backup_retention_limit_reached")
        try:
            for relative, digest in files.items():
                source = active_release.joinpath(*PurePosixPath(relative).parts)
                target = destination.joinpath(*PurePosixPath(relative).parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(source, target, follow_symlinks=False)
                if _hash_file(target) != digest:
                    raise DeploymentError("backup_verification_failed")
            metadata = {
                "update_id": update_id, "created_at": self._now(),
                "files": files, "sha256": _hash_json(files),
                "active_release": active_release.name,
            }
            _atomic_write(
                destination / "snapshot.json",
                json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
                0o600,
            )
        except (OSError, DeploymentError):
            if destination.exists():
                shutil.rmtree(destination)
            raise
        return destination

    def _snapshot_bytes_required(self, files: dict[str, str] | None = None) -> int:
        files = files if files is not None else self._enumerate_app_files()
        total = 1024 * 1024
        active_release = self._active_release_path()
        for relative in files:
            path = active_release.joinpath(*PurePosixPath(relative).parts)
            total += path.stat().st_size
        return total

    def _release_capacity_available(self, projected_release_bytes: int) -> bool:
        required = projected_release_bytes + MAX_RELEASE_BYTES
        try:
            return shutil.disk_usage(self.paths.releases_root).free >= required
        except OSError:
            return False

    def _backup_capacity_available(self, projected_snapshot_bytes: int = 0) -> bool:
        backups = [
            path for path in self.paths.backup_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ]
        if len(backups) >= MAX_BACKUP_SNAPSHOTS:
            return False
        total_size = 0
        for backup in backups:
            for path in backup.rglob("*"):
                if path.is_symlink():
                    return False
                if path.is_file():
                    total_size += path.stat().st_size
                    if total_size >= MAX_BACKUP_STORAGE_BYTES:
                        return False
        return total_size + projected_snapshot_bytes <= MAX_BACKUP_STORAGE_BYTES

    def _prepare_versioned_release(
        self,
        update_id: str,
        manifest_hash: str,
        manifest: dict[str, Any],
        staged_root: Path,
    ) -> Path:
        release_id = manifest_hash
        if not _SHA256.fullmatch(release_id):
            raise DeploymentError("release_id_invalid")
        destination = self.paths.releases_root / release_id
        if destination.exists() or destination.is_symlink():
            raise DeploymentError("release_already_exists")
        temporary = self.paths.releases_root / f".{release_id}.{update_id}.tmp"
        if temporary.exists() or temporary.is_symlink():
            raise DeploymentError("release_temporary_path_exists")
        active_release = self._active_release_path()
        source_package = active_release / "src" / "enan_ultra"
        candidate_package = temporary / "src" / "enan_ultra"
        temporary.mkdir(mode=0o700)
        try:
            for current in sorted(source_package.rglob("*")):
                if current.is_symlink():
                    raise DeploymentError("application_release_symlink_rejected")
                relative = current.relative_to(active_release / "src" / "enan_ultra")
                if "__pycache__" in relative.parts:
                    continue
                target = candidate_package / relative
                if current.is_dir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if not current.is_file():
                    raise DeploymentError("application_release_special_file")
                if current.suffix.lower() in {".pyc", ".pyo"}:
                    continue
                if current.suffix.lower() not in ALLOWED_SUFFIXES:
                    raise DeploymentError("application_release_unexpected_file")
                if not target.exists():
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copyfile(current, target, follow_symlinks=False)

            for item in sorted(manifest["files"], key=lambda value: value["path"]):
                path = item["path"]
                if not path.startswith(ALLOWED_PREFIX) or item["action"] == "unchanged":
                    continue
                target = temporary.joinpath(*PurePosixPath(path).parts)
                if item["action"] == "removed":
                    if target.is_symlink() or (target.exists() and not target.is_file()):
                        raise DeploymentError("candidate_path_unsafe")
                    target.unlink(missing_ok=True)
                elif item["action"] in {"added", "changed"}:
                    staged = staged_root.joinpath(*PurePosixPath(path).parts)
                    if staged.is_symlink() or not staged.is_file():
                        raise DeploymentError("staged_file_missing")
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    _atomic_write(target, staged.read_bytes(), 0o600)

            expected = {
                item["path"]: item["sha256"]
                for item in manifest["files"]
                if item["path"].startswith(ALLOWED_PREFIX) and item["action"] != "removed"
            }
            if self._enumerate_release_files(temporary) != expected:
                raise DeploymentError("candidate_release_verification_failed")
            for current in sorted(
                temporary.rglob("*"), key=lambda path: len(path.parts), reverse=True,
            ):
                if current.is_symlink():
                    raise DeploymentError("candidate_release_symlink_rejected")
                if current.is_file():
                    if os.name != "nt":
                        current.chmod(0o444)
                elif current.is_dir() and os.name != "nt":
                    current.chmod(0o555)
            if os.name != "nt":
                temporary.chmod(0o555)
            os.replace(temporary, destination)
            self._fsync_directory(self.paths.releases_root)
            return destination
        except Exception:
            if temporary.exists() and not temporary.is_symlink():
                if os.name != "nt":
                    for current in temporary.rglob("*"):
                        if current.is_dir() and not current.is_symlink():
                            current.chmod(0o700)
                    temporary.chmod(0o700)
                shutil.rmtree(temporary)
            raise

    def _activate_release(self, release: Path) -> None:
        resolved = release.resolve(strict=True)
        releases_root = self.paths.releases_root.resolve(strict=True)
        if resolved.parent != releases_root or resolved.is_symlink() or not resolved.is_dir():
            raise DeploymentError("release_activation_target_invalid")
        relative = f"releases/{resolved.name}"
        if os.name == "nt" and not self.require_root:
            _atomic_write(
                self.paths.app_root / ".current-release", relative.encode("utf-8"), 0o600,
            )
            return
        current = self.paths.current_release
        if current.exists() and not current.is_symlink():
            raise DeploymentError("current_release_pointer_invalid")
        temporary_link = self.paths.app_root / f".current-{uuid.uuid4().hex}.tmp"
        try:
            temporary_link.symlink_to(relative, target_is_directory=True)
            os.replace(temporary_link, current)
            self._fsync_directory(self.paths.app_root)
        finally:
            temporary_link.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _restore_snapshot(self, snapshot: Path) -> None:
        if snapshot.is_symlink() or not snapshot.is_dir():
            raise DeploymentError("backup_missing")
        try:
            metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError("backup_manifest_invalid") from exc
        if not isinstance(metadata, dict):
            raise DeploymentError("backup_manifest_invalid")
        files = metadata.get("files")
        if (
            not isinstance(files, dict)
            or any(
                not isinstance(relative, str) or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
                for relative, digest in files.items()
            )
            or _hash_json(files) != metadata.get("sha256")
        ):
            raise DeploymentError("backup_manifest_tampered")
        expected = set(files)
        for path in snapshot.rglob("*"):
            if path.is_symlink():
                raise DeploymentError("backup_symlink_rejected")
        for relative, digest in files.items():
            if not self._safe_manifest_path(relative) or not relative.startswith(ALLOWED_PREFIX):
                raise DeploymentError("backup_path_rejected")
            source = snapshot.joinpath(*PurePosixPath(relative).parts)
            if source.is_symlink() or not source.is_file() or _hash_file(source) != digest:
                raise DeploymentError("backup_hash_mismatch")
        active_name = metadata.get("active_release")
        if (
            not isinstance(active_name, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", active_name)
        ):
            raise DeploymentError("backup_release_identity_invalid")
        previous_release = self.paths.releases_root / active_name
        if not previous_release.is_symlink() and previous_release.is_dir():
            try:
                if self._enumerate_release_files(previous_release) == files:
                    self._activate_release(previous_release)
                    return
            except DeploymentError:
                pass
        recovery_release = self.paths.releases_root / f"rollback-{uuid.uuid4().hex}"
        recovery_release.mkdir(mode=0o700)
        try:
            for relative in sorted(expected):
                source = snapshot.joinpath(*PurePosixPath(relative).parts)
                target = recovery_release.joinpath(*PurePosixPath(relative).parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(source, target, follow_symlinks=False)
                if os.name != "nt":
                    target.chmod(0o444)
            for current_path in sorted(recovery_release.rglob("*"), reverse=True):
                if current_path.is_dir() and os.name != "nt":
                    current_path.chmod(0o555)
            if os.name != "nt":
                recovery_release.chmod(0o555)
            if self._enumerate_release_files(recovery_release) != files:
                raise DeploymentError("rollback_verification_failed")
            self._activate_release(recovery_release)
        except Exception:
            active_release: Path | None
            try:
                active_release = self._active_release_path()
            except DeploymentError:
                active_release = None
            if recovery_release.exists() and recovery_release.resolve() != active_release:
                if os.name != "nt":
                    for current in recovery_release.rglob("*"):
                        if current.is_dir() and not current.is_symlink():
                            current.chmod(0o700)
                    recovery_release.chmod(0o700)
                shutil.rmtree(recovery_release)
            raise

    def _wait_healthy(
        self, old_pid: int, expected_bot_id: int, deadline: float,
    ) -> int:
        while self.clock() < deadline:
            try:
                details = self.systemd.show()
                pid = int(details.get("MainPID", "0") or 0)
                marker = json.loads(self.paths.health_marker.read_text(encoding="utf-8"))
                expected_import = str((
                    self._active_release_path() / "src" / "enan_ultra" / "runtime.py"
                ).resolve(strict=True))
                if (
                    details.get("ActiveState") == "active"
                    and pid > 0 and pid != old_pid
                    and marker.get("pid") == pid
                    and marker.get("database_open") is True
                    and marker.get("telegram_bot_id") == expected_bot_id
                    and float(marker.get("started_at", 0)) >= deadline - self.health_timeout
                    and marker.get("import_origin") == expected_import
                ):
                    return pid
            except (DeploymentError, OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            self.sleep(1)
        raise DeploymentError("application_health_check_failed")

    def _acquire_os_lock(self) -> Any:
        handle = self.paths.lock_path.open("a+b")
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                handle.close()
                raise DeploymentError("deployment_already_running") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise DeploymentError("deployment_already_running") from exc
        return handle

    @staticmethod
    def _release_os_lock(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def _write_journal(self, update_id: str, data: dict[str, Any]) -> None:
        self._write_status(update_id, data)

    def _write_status(self, update_id: str, data: dict[str, Any]) -> None:
        with self._status_lock:
            self._validate_state_paths()
            _atomic_write(
                self.paths.history_root / f"{update_id}.json",
                json.dumps(data, sort_keys=True, separators=(",", ":")).encode(),
                0o600,
            )

    def _append_audit(
        self, update_id: str, administrator_id: int, state: str, manifest_hash: str,
        *, operator_uid: int | None = None,
    ) -> None:
        self._validate_state_paths()
        path = self.paths.history_root / "audit.jsonl"
        with path.open("a", encoding="utf-8") as output:
            record = {
                "update_id": update_id,
                "state": state,
                "manifest_hash": manifest_hash,
                "timestamp": self._now(),
            }
            identity_key = (
                "claimed_administrator_id"
                if state in {"awaiting_operator_authorization", "operator_approval_expired"}
                else "administrator_id"
            )
            record[identity_key] = administrator_id
            if operator_uid is not None:
                record["operator_uid"] = operator_uid
                record["approval_method"] = "root_unix_peer_and_interactive_operator"
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        if os.name != "nt":
            path.chmod(0o600)

    def status(self, update_id: str) -> dict[str, Any] | None:
        if not _UPDATE_ID.fullmatch(update_id):
            raise DeploymentError("invalid_update_id")
        with self._status_lock:
            self._validate_state_paths()
            path = self.paths.history_root / f"{update_id}.json"
            if not path.exists():
                return None
            return self._public_status(update_id)

    def recent(self) -> list[dict[str, Any]]:
        with self._status_lock:
            self._validate_state_paths()
            if not self.paths.history_root.is_dir():
                return []
            records: list[dict[str, Any]] = []
            for path in sorted(self.paths.history_root.glob("????????.json")):
                if path.is_symlink():
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if data.get("state") in {"succeeded", "rolled_back", "rollback_failed", "failed"}:
                    if data.get("notified") is not True:
                        records.append(self._public_status(str(data.get("update_id", ""))))
            return [record for record in records if record][-10:]

    def acknowledge(self, update_id: str) -> bool:
        with self._status_lock:
            current = self.status(update_id)
            if not current or current.get("state") not in {
                "succeeded", "rolled_back", "rollback_failed", "failed",
            }:
                return False
            data = json.loads((self.paths.history_root / f"{update_id}.json").read_text())
            if data.get("notified") is True:
                return False
            data["notified"] = True
            data["updated_at"] = self._now()
            self._write_status(update_id, data)
            return True

    def _public_status(self, update_id: str) -> dict[str, Any] | None:
        path = self.paths.history_root / f"{update_id}.json"
        if not path.is_file() or path.is_symlink():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = {
            "update_id", "state", "manifest_hash", "administrator_id",
            "claimed_administrator_id", "authorized_by_uid",
            "updated_at", "notified", "failure_code", "active_pid", "snapshot",
        }
        output = {key: data[key] for key in allowed if key in data}
        failure_code = output.get("failure_code")
        if failure_code is not None and (
            not isinstance(failure_code, str)
            or not re.fullmatch(r"[a-z0-9_]{1,64}", failure_code)
        ):
            output["failure_code"] = "deployment_failed"
        return output

    def _now(self) -> str:
        return datetime.fromtimestamp(self.clock(), UTC).isoformat()


class DeploymentRequestHandler(socketserver.StreamRequestHandler):
    deployer: ReleaseDeployer
    allowed_uid: int

    def handle(self) -> None:
        if not hasattr(socket, "SO_PEERCRED"):
            self._respond({"ok": False, "error": "peer_credentials_unavailable"})
            return
        _pid, uid, _gid = __import__("struct").unpack(
            "3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
        )
        if uid not in {0, self.allowed_uid}:
            self._respond({"ok": False, "error": "unauthorized_peer"})
            return
        raw = self.rfile.readline(REQUEST_LIMIT + 1)
        if len(raw) > REQUEST_LIMIT or not raw.endswith(b"\n"):
            self._respond({"ok": False, "error": "invalid_request"})
            return
        peer_uid = uid
        try:
            request = json.loads(raw)
            response = self._dispatch(request, peer_uid=peer_uid)
        except DeploymentError as exc:
            response = {
                "ok": False,
                "error": exc.code if peer_uid == 0 else "request_rejected",
            }
        except (ValueError, TypeError):
            response = {"ok": False, "error": "request_rejected"}
        self._respond(response)

    def _dispatch(self, request: Any, *, peer_uid: int) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise DeploymentError("invalid_request")
        operation = request.get("operation")
        if operation in {"approval_details", "authorize"} and peer_uid != 0:
            raise DeploymentError("unauthorized_operator")
        if operation == "preflight":
            return {"ok": True, **self.deployer.preflight()}
        if operation == "request_deploy":
            if peer_uid != self.allowed_uid:
                raise DeploymentError("unauthorized_service_peer")
            if "administrator_id" in request:
                raise DeploymentError("caller_identity_field_forbidden")
            update_id = request.get("update_id")
            manifest_hash = request.get("manifest_hash")
            archive_hash = request.get("archive_hash")
            claimed_administrator_id = request.get("claimed_administrator_id")
            self.deployer._validate_request(
                update_id, manifest_hash, archive_hash, claimed_administrator_id,
            )
            accepted = self.deployer.request_deployment(
                update_id, manifest_hash, archive_hash, claimed_administrator_id,
            )
            if not accepted:
                return {"ok": False, "error": "duplicate_or_replayed_request"}
            return {
                "ok": True, "accepted": True, "state": "awaiting_operator_authorization",
                "update_id": update_id,
            }
        if operation == "approval_details":
            update_id = request.get("update_id")
            return {
                "ok": True,
                "approval": self.deployer.pending_operator_approval(update_id),
            }
        if operation == "authorize":
            update_id = request.get("update_id")
            manifest_hash = request.get("manifest_hash")
            if not isinstance(update_id, str) or not isinstance(manifest_hash, str):
                raise DeploymentError("invalid_operator_approval")
            accepted = self.deployer.authorize_pending_deployment(
                update_id, manifest_hash, operator_uid=peer_uid,
            )
            return {"ok": True, "authorized": accepted, "update_id": update_id}
        if operation == "status":
            update_id = request.get("update_id")
            status = self.deployer.status(update_id)
            return {"ok": True, "status": status}
        if operation == "recent":
            return {"ok": True, "results": self.deployer.recent()}
        if operation == "ack":
            update_id = request.get("update_id")
            return {"ok": True, "acknowledged": self.deployer.acknowledge(update_id)}
        raise DeploymentError("unknown_operation")

    def _respond(self, response: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        self.wfile.flush()


class DeploymentServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = getattr(socket, "AF_UNIX", socket.AF_INET)
    socket_type = socket.SOCK_STREAM

    def __init__(
        self, address: str, deployer: ReleaseDeployer, allowed_uid: int,
        listener: socket.socket | None = None,
    ) -> None:
        self.RequestHandlerClass.deployer = deployer
        self.RequestHandlerClass.allowed_uid = allowed_uid
        super().__init__(
            address, self.RequestHandlerClass, bind_and_activate=listener is None,
        )
        if listener is not None:
            self.socket.close()
            self.socket = listener
            self.server_address = address


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _operator_socket_request(socket_path: Path, payload: dict[str, object]) -> dict[str, Any]:
    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        raise SystemExit("Root operator authorization is available only on Linux.")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(15)
    try:
        connection.connect(str(socket_path))
        connection.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
        with connection.makefile("rb") as response_file:
            raw = response_file.readline(REQUEST_LIMIT + 1)
        if not raw or len(raw) > REQUEST_LIMIT or not raw.endswith(b"\n"):
            raise SystemExit("The deployment broker returned an invalid response.")
        response = json.loads(raw)
        if not isinstance(response, dict) or response.get("ok") is not True:
            error = response.get("error") if isinstance(response, dict) else "invalid_response"
            raise SystemExit(
                f"The deployment broker rejected operator authorization ({str(error)[:80]})."
            )
        return response
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit("The deployment broker socket is unavailable or returned invalid data.") from exc
    finally:
        connection.close()


def _confirm_operator_approval(
    update_id: str,
    approval: dict[str, Any],
    *,
    input_fn: Any = None,
    output_fn: Any = None,
) -> str:
    read = input_fn or input
    write = output_fn or print
    manifest_hash = approval.get("manifest_hash")
    archive_hash = approval.get("archive_hash")
    claimed_admin = approval.get("claimed_administrator_id")
    if (
        approval.get("update_id") != update_id
        or approval.get("operation") != "deploy"
        or not isinstance(manifest_hash, str) or not _SHA256.fullmatch(manifest_hash)
        or not isinstance(archive_hash, str) or not _SHA256.fullmatch(archive_hash)
        or not isinstance(claimed_admin, int) or isinstance(claimed_admin, bool)
    ):
        raise SystemExit("The broker returned incomplete or mismatched approval details.")
    write(f"Update ID: {update_id}")
    write("Operation: deploy")
    write(f"Candidate manifest SHA-256: {manifest_hash}")
    write(f"Candidate archive SHA-256: {archive_hash}")
    write(f"Claimed Telegram administrator ID: {claimed_admin}")
    write("Verify the actual Telegram approval in the administrator chat and compare the full manifest digest.")
    verified_admin = read("Telegram administrator ID you verified: ").strip()
    if verified_admin != str(claimed_admin):
        raise SystemExit("The independently verified Telegram administrator ID did not match.")
    confirmation = read(f"Type DEPLOY {update_id} {manifest_hash}: ").strip()
    if confirmation != f"DEPLOY {update_id} {manifest_hash}":
        raise SystemExit("Exact release confirmation did not match; nothing was authorized.")
    return manifest_hash


def _authorize_from_operator_console(socket_path: Path, update_id: str) -> None:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise SystemExit("Run this approval command from a root operator shell.")
    if not sys.stdin.isatty():
        raise SystemExit("Interactive terminal confirmation is required for operator approval.")
    if not _UPDATE_ID.fullmatch(update_id):
        raise SystemExit("Invalid update ID.")
    response = _operator_socket_request(
        socket_path, {"operation": "approval_details", "update_id": update_id},
    )
    approval = response.get("approval")
    if not isinstance(approval, dict):
        raise SystemExit("The broker returned invalid approval details.")
    manifest_hash = _confirm_operator_approval(update_id, approval)
    result = _operator_socket_request(
        socket_path,
        {
            "operation": "authorize",
            "update_id": update_id,
            "manifest_hash": manifest_hash,
        },
    )
    if result.get("authorized") is not True:
        raise SystemExit("The broker did not authorize the deployment.")
    print("One-time root operator approval accepted; the broker will verify the candidate before activation.")


def serve() -> None:
    if os.geteuid() != 0:
        raise SystemExit("The deployment broker must run as root.")
    if not hasattr(socket, "SO_PEERCRED"):
        raise SystemExit("This deployment broker requires Linux Unix-socket peer credentials.")
    import pwd

    try:
        allowed_uid = pwd.getpwnam(SERVICE_USER).pw_uid
    except KeyError as exc:
        raise SystemExit("The configured bot service account does not exist.") from exc
    listener_fds = int(os.environ.get("LISTEN_FDS", "0"))
    listener_pid = int(os.environ.get("LISTEN_PID", "0"))
    if listener_fds != 1 or listener_pid != os.getpid():
        raise SystemExit("The deployment broker must be started by its systemd socket unit.")
    listener = socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)
    deployer = ReleaseDeployer()
    deployer.recover_interrupted_deployments()
    server = DeploymentServer(str(SOCKET_PATH), deployer, allowed_uid, listener)
    server.serve_forever(poll_interval=0.5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--authorize", metavar="UPDATE_ID")
    parser.add_argument("--socket-path", type=Path, default=SOCKET_PATH)
    args = parser.parse_args()
    if args.serve and args.authorize:
        parser.error("--serve and --authorize cannot be combined")
    if args.serve:
        serve()
        return 0
    if args.authorize:
        _authorize_from_operator_console(args.socket_path, args.authorize)
        return 0
    parser.error("use --serve or --authorize UPDATE_ID")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
