#!/usr/bin/env python3
"""Root-owned, allowlisted deployment broker for the Telegram ZIP updater."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import socketserver
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SERVICE_NAME = "telegram-ai.service"
APP_ROOT = Path("/opt/enan-ultra")
BOT_STATE_ROOT = Path("/var/lib/telegram-ai")
DEPLOY_STATE_ROOT = Path("/var/lib/telegram-ai-deploy")
SOCKET_PATH = Path("/run/telegram-ai-deploy.sock")
ENABLE_FLAG = Path("/etc/telegram-ai/zip-deploy.enabled")
SERVICE_USER = "enan-ultra"
HEALTH_MARKER = BOT_STATE_ROOT / "runtime-health.json"
STAGING_ROOT = BOT_STATE_ROOT / ".telegram-zip-updates"
BACKUP_ROOT = DEPLOY_STATE_ROOT / "release-backups"
HISTORY_ROOT = DEPLOY_STATE_ROOT / "deployment-history"
LOCK_PATH = DEPLOY_STATE_ROOT / "deployment.lock"
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
MAX_RELEASE_FILES = 250
MAX_BACKUP_SNAPSHOTS = 5
MAX_BACKUP_STORAGE_BYTES = 500 * 1024 * 1024
REQUEST_LIMIT = 16 * 1024
HEALTH_TIMEOUT_SECONDS = 90
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
    state_root: Path = DEPLOY_STATE_ROOT
    staging_root: Path = STAGING_ROOT
    backup_root: Path = BACKUP_ROOT
    history_root: Path = HISTORY_ROOT
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

    def submit(
        self,
        update_id: str,
        manifest_hash: str,
        archive_hash: str,
        administrator_id: int,
    ) -> bool:
        self._validate_request(update_id, manifest_hash, archive_hash, administrator_id)
        self._validate_state_paths()
        with self._submission_lock:
            current = self.status(update_id)
            if current:
                return False
            self._write_status(update_id, {
                "update_id": update_id, "state": "queued",
                "manifest_hash": manifest_hash, "administrator_id": administrator_id,
                "updated_at": self._now(), "notified": False,
            })
            worker = threading.Thread(
                target=self._run_submitted,
                args=(update_id, manifest_hash, archive_hash, administrator_id),
                daemon=True,
                name=f"deploy-{update_id}",
            )
            worker.start()
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
        except Exception:
            self._write_status(update_id, {
                **(self.status(update_id) or {}),
                "update_id": update_id, "state": "failed",
                "manifest_hash": manifest_hash, "administrator_id": administrator_id,
                "updated_at": self._now(), "failure_code": "deployment_failed",
                "notified": False,
            })

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
                activated = True
                self._write_journal(update_id, {
                    "update_id": update_id, "state": "applying",
                    "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                    "administrator_id": administrator_id, "updated_at": self._now(),
                    "telegram_bot_id": telegram_bot_id,
                })
                self._apply_release(
                    update_id, manifest, release_root=private_update / "release",
                )
                self._write_journal(update_id, {
                    "update_id": update_id, "state": "restarting",
                    "manifest_hash": manifest_hash, "snapshot": str(snapshot),
                    "administrator_id": administrator_id, "updated_at": self._now(),
                    "telegram_bot_id": telegram_bot_id,
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
        if self.paths.app_root.is_symlink() or not self.paths.app_root.is_dir():
            raise DeploymentError("application_path_invalid")
        if self.require_root:
            app_stat = self.paths.app_root.stat()
            if app_stat.st_uid != 0 or stat.S_IMODE(app_stat.st_mode) & 0o022:
                raise DeploymentError("application_root_permissions_invalid")
        details = self.systemd.show()
        exec_start = details.get("ExecStart", "")
        expected_python = str(self.paths.app_root / ".venv" / "bin" / "python")
        if (
            details.get("User") != SERVICE_USER
            or details.get("WorkingDirectory") != str(self.paths.app_root)
            or not self._service_entrypoint_matches(exec_start, expected_python)
        ):
            raise DeploymentError("service_layout_mismatch")

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
            if self.require_root and path.stat().st_uid != 0:
                raise DeploymentError("deployment_state_not_root_owned")

    def _check_baseline_health(self) -> int:
        details = self.systemd.show()
        if details.get("ActiveState") != "active" or details.get("MainPID", "0") == "0":
            raise DeploymentError("service_not_active")
        try:
            marker = json.loads(self.paths.health_marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError("baseline_health_missing") from exc
        if (
            marker.get("pid") != int(details["MainPID"])
            or marker.get("database_open") is not True
            or not isinstance(marker.get("telegram_bot_id"), int)
            or float(marker.get("started_at", 0)) <= 0
        ):
            raise DeploymentError("baseline_health_invalid")
        return int(marker["telegram_bot_id"])

    @staticmethod
    def _validate_request(
        update_id: str, manifest_hash: str, archive_hash: str, administrator_id: int,
    ) -> None:
        if (
            not _UPDATE_ID.fullmatch(update_id)
            or not _SHA256.fullmatch(manifest_hash)
            or not _SHA256.fullmatch(archive_hash)
            or administrator_id <= 0
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
        if (
            manifest.get("schema") != 2
            or manifest.get("update_id") != update_id
            or manifest.get("archive_sha256") != archive_hash
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
        root = self.paths.app_root / "src" / "enan_ultra"
        if root.is_symlink() or not root.is_dir():
            raise DeploymentError("application_package_missing")
        result: dict[str, str] = {}
        for current in root.rglob("*"):
            if "__pycache__" in current.parts:
                continue
            if current.is_symlink():
                raise DeploymentError("live_application_symlink_rejected")
            if current.is_dir():
                if self.require_root and (
                    current.stat().st_uid != 0
                    or stat.S_IMODE(current.stat().st_mode) & 0o022
                ):
                    raise DeploymentError("live_application_permissions_invalid")
                if current.name == "__pycache__":
                    continue
                continue
            if not current.is_file():
                raise DeploymentError("live_application_special_file")
            if current.suffix.lower() not in ALLOWED_SUFFIXES:
                if current.suffix.lower() in {".pyc", ".pyo"}:
                    continue
                raise DeploymentError("live_application_unexpected_file")
            if self.require_root and (
                current.stat().st_uid != 0 or stat.S_IMODE(current.stat().st_mode) & 0o022
            ):
                raise DeploymentError("live_application_permissions_invalid")
            relative = current.relative_to(self.paths.app_root).as_posix()
            result[relative] = _hash_file(current)
        return result

    def _create_snapshot(self, update_id: str) -> Path:
        stamp = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = self.paths.backup_root / f"{stamp}-{update_id}"
        if destination.exists() or destination.is_symlink():
            raise DeploymentError("backup_destination_exists")
        files = self._enumerate_app_files()
        required_bytes = self._snapshot_bytes_required(files)
        if not self._backup_capacity_available(required_bytes):
            raise DeploymentError("backup_retention_limit_reached")
        try:
            for relative, digest in files.items():
                source = self.paths.app_root.joinpath(*PurePosixPath(relative).parts)
                target = destination.joinpath(*PurePosixPath(relative).parts)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(source, target, follow_symlinks=False)
                if _hash_file(target) != digest:
                    raise DeploymentError("backup_verification_failed")
            metadata = {
                "update_id": update_id, "created_at": self._now(),
                "files": files, "sha256": _hash_json(files),
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
        for relative in files:
            path = self.paths.app_root.joinpath(*PurePosixPath(relative).parts)
            total += path.stat().st_size
        return total

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

    def _apply_release(
        self,
        update_id: str,
        manifest: dict[str, Any],
        *,
        release_root: Path | None = None,
    ) -> None:
        root = release_root or self.paths.staging_root / update_id / "release"
        for item in sorted(manifest["files"], key=lambda value: value["path"]):
            path = item["path"]
            if not path.startswith(ALLOWED_PREFIX) or item["action"] == "unchanged":
                continue
            target = self.paths.app_root.joinpath(*PurePosixPath(path).parts)
            self._ensure_contained(target.parent)
            if item["action"] == "removed":
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise DeploymentError("target_path_unsafe")
                target.unlink(missing_ok=True)
                continue
            staged = root.joinpath(*PurePosixPath(path).parts)
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            content = staged.read_bytes()
            _atomic_write(target, content, 0o644)

    def _ensure_contained(self, directory: Path) -> None:
        resolved_root = self.paths.app_root.resolve()
        cursor = directory
        while cursor != resolved_root:
            if cursor.is_symlink():
                raise DeploymentError("target_path_unsafe")
            parent = cursor.parent
            if parent == cursor:
                raise DeploymentError("target_path_unsafe")
            cursor = parent
        if directory.resolve() != directory:
            raise DeploymentError("target_path_unsafe")

    def _restore_snapshot(self, snapshot: Path) -> None:
        if snapshot.is_symlink() or not snapshot.is_dir():
            raise DeploymentError("backup_missing")
        try:
            metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError("backup_manifest_invalid") from exc
        files = metadata.get("files")
        if not isinstance(files, dict) or _hash_json(files) != metadata.get("sha256"):
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
        current = self._enumerate_app_files()
        for relative in set(current) - expected:
            self.paths.app_root.joinpath(*PurePosixPath(relative).parts).unlink()
        for relative in sorted(expected):
            source = snapshot.joinpath(*PurePosixPath(relative).parts)
            target = self.paths.app_root.joinpath(*PurePosixPath(relative).parts)
            self._ensure_contained(target.parent)
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            _atomic_write(target, source.read_bytes(), 0o644)
        if self._enumerate_app_files() != files:
            raise DeploymentError("rollback_verification_failed")

    def _wait_healthy(
        self, old_pid: int, expected_bot_id: int, deadline: float,
    ) -> int:
        while self.clock() < deadline:
            try:
                details = self.systemd.show()
                pid = int(details.get("MainPID", "0") or 0)
                marker = json.loads(self.paths.health_marker.read_text(encoding="utf-8"))
                if (
                    details.get("ActiveState") == "active"
                    and pid > 0 and pid != old_pid
                    and marker.get("pid") == pid
                    and marker.get("database_open") is True
                    and marker.get("telegram_bot_id") == expected_bot_id
                    and float(marker.get("started_at", 0)) >= deadline - self.health_timeout
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
        self._validate_state_paths()
        _atomic_write(
            self.paths.history_root / f"{update_id}.json",
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode(),
            0o600,
        )

    def _append_audit(
        self, update_id: str, administrator_id: int, state: str, manifest_hash: str,
    ) -> None:
        self._validate_state_paths()
        path = self.paths.history_root / "audit.jsonl"
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({
                "update_id": update_id, "administrator_id": administrator_id,
                "state": state, "manifest_hash": manifest_hash,
                "timestamp": self._now(),
            }, sort_keys=True, separators=(",", ":")) + "\n")
        if os.name != "nt":
            path.chmod(0o600)

    def status(self, update_id: str) -> dict[str, Any] | None:
        if not _UPDATE_ID.fullmatch(update_id):
            raise DeploymentError("invalid_update_id")
        self._validate_state_paths()
        path = self.paths.history_root / f"{update_id}.json"
        if not path.exists():
            return None
        return self._public_status(update_id)

    def recent(self) -> list[dict[str, Any]]:
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
        if hasattr(socket, "SO_PEERCRED"):
            _pid, uid, _gid = __import__("struct").unpack(
                "3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
            )
            if uid != self.allowed_uid:
                self._respond({"ok": False, "error": "unauthorized_peer"})
                return
        raw = self.rfile.readline(REQUEST_LIMIT + 1)
        if len(raw) > REQUEST_LIMIT or not raw.endswith(b"\n"):
            self._respond({"ok": False, "error": "invalid_request"})
            return
        try:
            request = json.loads(raw)
            response = self._dispatch(request)
        except (ValueError, TypeError, DeploymentError):
            response = {"ok": False, "error": "request_rejected"}
        self._respond(response)

    def _dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise DeploymentError("invalid_request")
        operation = request.get("operation")
        if operation == "preflight":
            return {"ok": True, **self.deployer.preflight()}
        if operation == "deploy":
            update_id = request.get("update_id")
            manifest_hash = request.get("manifest_hash")
            archive_hash = request.get("archive_hash")
            administrator_id = request.get("administrator_id")
            self.deployer._validate_request(
                update_id, manifest_hash, archive_hash, administrator_id,
            )
            accepted = self.deployer.submit(
                update_id, manifest_hash, archive_hash, administrator_id,
            )
            if not accepted:
                return {"ok": False, "error": "approval_expired_or_already_used"}
            return {"ok": True, "accepted": True, "update_id": update_id}
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
    args = parser.parse_args()
    if not args.serve:
        parser.error("only --serve is supported")
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
