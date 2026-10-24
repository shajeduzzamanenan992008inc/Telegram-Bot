"""Isolated, review-only staging for administrator-supplied project ZIP files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
import unicodedata
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import BinaryIO, Protocol

from enan_ultra.config import Settings

_UPLOAD_TTL_SECONDS = 5 * 60
_REVIEW_TTL_SECONDS = 30 * 60
_MAX_ACTIVE_RELEASES = 1
_ALLOWED_TOP_LEVEL_FILES = frozenset({
    ".env.example", "changelog.md", "pyproject.toml", "readme.md",
    "requirements.txt", "uv.lock",
})
_ALLOWED_DIRECTORIES = frozenset({"docs", "src/enan_ultra", "tests"})
_ALLOWED_PREFIXES = tuple(f"{path}/" for path in _ALLOWED_DIRECTORIES)
_PROTECTED_NAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519",
    "authorized_keys", "credentials.json", "secrets.json",
})
_PROTECTED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", "data", "uploads",
    "user_uploads", "backups", "runtime", "logs", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".ssh",
    ".aws", ".azure", ".kube", ".terraform", "secrets", "credentials",
    "certs", "certificates",
})
_SOURCE_SUFFIXES = frozenset({".py", ".json", ".html", ".css", ".js"})
_TEST_SUFFIXES = frozenset({".py", ".json", ".txt", ".md", ".html", ".css", ".js"})
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_CHUNK_SIZE = 64 * 1024
_DEFAULT_EXTRACT_TIMEOUT_SECONDS = 60
_MAX_EXTRACT_TIMEOUT_SECONDS = 300
_MANIFEST_NAME = "manifest.json"
UPDATE_CALLBACK_HASH_PREFIX_LENGTH = 32
_DEPLOYMENT_PROTECTED_PATHS = frozenset({
    "src/enan_ultra/config.py",
    "src/enan_ultra/handlers.py",
    "src/enan_ultra/runtime.py",
    "src/enan_ultra/security.py",
    "src/enan_ultra/storage/database.py",
    "src/enan_ultra/services/telegram_zip_updates.py",
})
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)"
    r"(\s*[:=]\s*)([^\s,;]+)",
)
_TELEGRAM_TOKEN = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")
_OPENROUTER_KEY = re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b")
_GENERIC_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("ZIP inspection and extraction exceeded UPDATE_EXTRACT_TIMEOUT_SECONDS.")


@dataclass(frozen=True, slots=True)
class UploadSession:
    user_id: int
    chat_id: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class UpdateFile:
    path: str
    action: str
    size: int
    sha256: str
    previous_sha256: str | None = None


@dataclass(slots=True)
class StagedUpdate:
    update_id: str
    uploader_id: int
    chat_id: int
    archive_name: str
    archive_size: int
    archive_sha256: str
    source_archive_sha256: str
    archive_path: Path
    release_hash: str
    release_dir: Path
    manifest_path: Path
    files: tuple[UpdateFile, ...]
    dependency_changes: tuple[str, ...]
    security_warnings: tuple[str, ...]
    syntax_status: str
    syntax_summary: str
    test_status: str = "NOT RUN"
    test_summary: str = "Tests have not run yet."
    state: str = "testing"
    production_approved_by: int | None = None
    deployment_state: str = "not_requested"
    deployment_result: str = ""
    expires_at: float = field(default_factory=lambda: time.monotonic() + _REVIEW_TTL_SECONDS)


@dataclass(frozen=True, slots=True)
class _ArchiveEntry:
    info: zipfile.ZipInfo
    path: str
    is_directory: bool
    sha256: str


class _HashSink(Protocol):
    def update(self, data: bytes, /) -> None: ...


class _SandboxReport(Protocol):
    exit_code: int
    output: str
    timed_out: bool


class _TestSandbox(Protocol):
    async def run_python_tests(self, archive_data: bytes) -> _SandboxReport: ...


class TelegramZipUpdateManager:
    """Validate and stage approved-path files without touching the live project."""

    def __init__(
        self, settings: Settings, project_root: Path, staging_parent: Path,
    ) -> None:
        self.settings = settings
        self.project_root = project_root.resolve()
        self.staging_parent = staging_parent
        # ZIP updates use the separately configured, single ADMIN_ID. Keep this
        # identity independent from ADMIN_USER_IDS, which grants other bot rights.
        self.admin_ids = frozenset({settings.admin_id})
        self.admin_chat_id = settings.admin_chat_id
        self.deployment_enabled = bool(getattr(settings, "update_deployment_enabled", False))
        self.deployment_socket_path = Path(getattr(
            settings, "update_deployment_socket_path", "/run/telegram-ai-deploy.sock",
        ))
        self.upload_sessions: dict[int, UploadSession] = {}
        self.updates: dict[str, StagedUpdate] = {}
        self._lock = RLock()
        self._cleanup_orphaned_releases()

    def cleanup_expired(self) -> None:
        with self._lock:
            self._expire_locked()
            quarantine = self.staging_parent / "quarantine"
            if quarantine.is_symlink() or not quarantine.is_dir():
                return
            cutoff = time.time() - 24 * 60 * 60
            for path in quarantine.iterdir():
                try:
                    if (
                        path.suffix.lower() in {".zip", ".json"}
                        and path.is_file() and not path.is_symlink()
                        and path.stat().st_mtime < cutoff
                    ):
                        path.unlink()
                except OSError:
                    continue

    def quarantine_directory(self) -> Path:
        """Return the private holding directory for unscanned or invalid admin uploads."""
        with self._lock:
            self._create_staging_parent()
            quarantine = self.staging_parent / "quarantine"
            if quarantine.exists() and (quarantine.is_symlink() or not quarantine.is_dir()):
                raise RuntimeError("The update quarantine path is not a private directory.")
            quarantine.mkdir(mode=0o700, exist_ok=True)
            if quarantine.is_symlink() or not quarantine.is_dir():
                raise RuntimeError("The update quarantine path is not a private directory.")
            if os.name != "nt":
                quarantine.chmod(0o700)
            return quarantine

    def start_upload(self, user_id: int, chat_id: int) -> UploadSession:
        with self._lock:
            self._expire_locked()
            self._authorize(user_id, chat_id)
            session = UploadSession(
                user_id, chat_id, time.monotonic() + _UPLOAD_TTL_SECONDS,
            )
            self.upload_sessions[user_id] = session
            return session

    def has_upload_session(self, user_id: int) -> bool:
        with self._lock:
            session = self.upload_sessions.get(user_id)
            if session and session.expires_at <= time.monotonic():
                self.upload_sessions.pop(user_id, None)
                return False
            return session is not None

    def cancel_upload(self, user_id: int, chat_id: int) -> bool:
        with self._lock:
            self._authorize(user_id, chat_id)
            session = self.upload_sessions.get(user_id)
            if not session or session.chat_id != chat_id or session.expires_at <= time.monotonic():
                self.upload_sessions.pop(user_id, None)
                return False
            self.upload_sessions.pop(user_id, None)
            return True

    def prepare(
        self,
        user_id: int,
        chat_id: int,
        archive_name: str,
        archive_data: bytes,
    ) -> StagedUpdate:
        with self._lock:
            self._expire_locked()
            self._authorize(user_id, chat_id)
            session = self.upload_sessions.get(user_id)
            if not session or session.chat_id != chat_id:
                raise ValueError("No active /update upload session exists in this chat.")
            if session.expires_at <= time.monotonic():
                self.upload_sessions.pop(user_id, None)
                raise ValueError("The /update upload session expired; start again with /update.")
            if sum(
                update.state in {"testing", "review", "approved"}
                for update in self.updates.values()
            ) >= _MAX_ACTIVE_RELEASES:
                raise ValueError("Another ZIP update is already staged; review, reject, or cancel it first.")
            self.upload_sessions.pop(user_id, None)
            return self._prepare_locked(user_id, chat_id, archive_name, archive_data)

    async def run_tests(self, update: StagedUpdate, sandbox: _TestSandbox | None) -> None:
        if update.syntax_status != "PASS":
            update.test_status = "BLOCKED"
            update.test_summary = "Isolated tests were blocked because Python syntax validation failed."
            update.state = "review"
            self._seal_validation(update)
            return
        if not self.settings.sandbox_enabled or sandbox is None:
            update.test_status = "BLOCKED"
            update.test_summary = (
                "Rootless Docker sandbox is disabled or unavailable; automated tests were not run."
            )
            update.state = "review"
            self._seal_validation(update)
            return
        try:
            archive_data = self._read_staged_archive(update)
            update_test_runner = getattr(sandbox, "run_update_tests", None)
            if callable(update_test_runner):
                report = await update_test_runner(archive_data)
            else:
                report = await sandbox.run_python_tests(archive_data)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            update.test_status = "BLOCKED"
            update.test_summary = (
                f"Isolated tests could not run ({type(exc).__name__}); no pass is claimed."
            )
            update.state = "review"
            self._seal_validation(update)
            return
        if report.timed_out or report.exit_code != 0:
            update.test_status = "FAIL"
            update.test_summary = (
                f"Sandbox exit={report.exit_code}; timed_out={report.timed_out}.\n"
                f"{redact_secrets(report.output)[-2500:]}"
            )
        else:
            update.test_status = "PASS"
            update.test_summary = (
                f"Sandbox exit=0; timed_out=false.\n{redact_secrets(report.output)[-2500:]}"
            )
        update.state = "review"
        self._seal_validation(update)

    def mark_tests_blocked(self, update: StagedUpdate, error: Exception) -> None:
        with self._lock:
            if update.state != "testing":
                raise ValueError("This staged update is no longer eligible for test results.")
            update.test_status = "BLOCKED"
            update.test_summary = (
                f"Isolated tests were blocked ({type(error).__name__}); no pass is claimed."
            )
            update.state = "review"
            self._seal_validation(update)

    def get_update(self, update_id: str, user_id: int, chat_id: int) -> StagedUpdate | None:
        with self._lock:
            self._authorize(user_id, chat_id)
            self._expire_locked()
            update = self.updates.get(update_id)
            if update and update.chat_id == chat_id and update.state != "expired":
                return update
            return None

    def decide(
        self,
        update_id: str,
        release_hash_prefix: str,
        user_id: int,
        chat_id: int,
        *,
        approve: bool,
    ) -> StagedUpdate:
        with self._lock:
            self._authorize(user_id, chat_id)
            self._expire_locked()
            update = self.updates.get(update_id)
            if not update or update.chat_id != chat_id or update.state != "review":
                raise ValueError("This staged update is missing, expired, or already handled.")
            if update.release_hash[:UPDATE_CALLBACK_HASH_PREFIX_LENGTH] != release_hash_prefix:
                raise ValueError("The approval does not match this exact staged release.")
            self._verify_release(update)
            if approve and (update.syntax_status != "PASS" or update.test_status != "PASS"):
                raise ValueError("Approval is blocked until syntax checks and isolated tests both pass.")
            if not approve:
                self._remove_update_files(update)
            update.state = "approved" if approve else "rejected"
            return update

    def cancel_staged(
        self, update_id: str, release_hash_prefix: str, user_id: int, chat_id: int,
    ) -> StagedUpdate:
        with self._lock:
            self._authorize(user_id, chat_id)
            self._expire_locked()
            update = self.updates.get(update_id)
            if not update or update.chat_id != chat_id or update.state not in {
                "testing", "review", "approved",
            }:
                raise ValueError("This staged update is missing, expired, or already handled.")
            if update.release_hash[:UPDATE_CALLBACK_HASH_PREFIX_LENGTH] != release_hash_prefix:
                raise ValueError("The cancellation does not match this staged release.")
            self._remove_update_files(update)
            update.state = "cancelled"
            return update

    def production_eligibility(
        self, update_id: str, release_hash_prefix: str, user_id: int, chat_id: int,
    ) -> tuple[StagedUpdate, tuple[str, ...]]:
        with self._lock:
            self._authorize(user_id, chat_id)
            self._expire_locked()
            update = self.updates.get(update_id)
            if (
                not update or update.chat_id != chat_id
                or update.state != "approved"
            ):
                raise ValueError(
                    "Only a staging-approved, unexpired release can be production-approved."
                )
            if update.release_hash[:UPDATE_CALLBACK_HASH_PREFIX_LENGTH] != release_hash_prefix:
                raise ValueError("The production approval does not match the reviewed manifest.")
            self._verify_release(update)
            reasons: list[str] = []
            if not self.deployment_enabled:
                reasons.append("Production deployment is disabled by configuration.")
            if update.syntax_status != "PASS" or update.test_status != "PASS":
                reasons.append("Mandatory syntax and isolated tests must both pass.")
            if update.dependency_changes:
                reasons.append("Dependency changes require a separate operator-managed release.")
            changed_control = sorted(
                item.path for item in update.files
                if item.action != "unchanged" and item.path in _DEPLOYMENT_PROTECTED_PATHS
            )
            if changed_control:
                reasons.append(
                    "Production approval policy/runtime control files are protected: "
                    + ", ".join(changed_control)
                )
            return update, tuple(reasons)

    def begin_production_deployment(
        self, update_id: str, release_hash_prefix: str, user_id: int, chat_id: int,
    ) -> StagedUpdate:
        with self._lock:
            update, reasons = self.production_eligibility(
                update_id, release_hash_prefix, user_id, chat_id,
            )
            if reasons:
                raise ValueError("Production deployment blocked: " + " ".join(reasons))
            if update.deployment_state != "not_requested":
                raise ValueError("This production approval has already been used.")
            self._verify_release(update)
            update.production_approved_by = user_id
            update.deployment_state = "submitting"
            update.deployment_result = (
                "Production approval recorded; the deployment request is being submitted."
            )
            return update

    def finish_production_deployment(
        self, update_id: str, *, accepted: bool, result: str,
    ) -> StagedUpdate:
        with self._lock:
            update = self.updates.get(update_id)
            if not update or update.deployment_state != "submitting":
                raise ValueError("This production deployment request is missing or already handled.")
            update.deployment_state = (
                "awaiting_operator_authorization" if accepted else "failed"
            )
            update.deployment_result = redact_secrets(result)[:300]
            if not accepted:
                update.state = "deployment_failed"
            return update

    def manifest_text(self, update: StagedUpdate) -> str:
        lines = [
            f"Update ID: {update.update_id}",
            f"Archive: {update.archive_name} ({update.archive_size} bytes)",
            f"Uploaded ZIP SHA-256: {update.source_archive_sha256}",
            f"Merged candidate ZIP SHA-256: {update.archive_sha256}",
            f"Release manifest SHA-256: {update.release_hash}",
            f"Workflow decision: {update.state.upper()}",
            f"Syntax check: {update.syntax_status} — {update.syntax_summary}",
            f"Isolated tests: {update.test_status} — {update.test_summary}",
            "Protected paths: PASS — only allowlisted application/documentation/test files were staged.",
            f"Production deployment state: {update.deployment_state.upper()} — "
            f"{redact_secrets(update.deployment_result)[:300] or 'not requested'}.",
            "Dependency changes: " + (", ".join(update.dependency_changes) or "none"),
            "Security warnings: " + ("; ".join(update.security_warnings) or "none"),
            "File changes:",
        ]
        lines.extend(
            f"{entry.action.upper():7} {entry.path} · {entry.size} bytes · sha256 {entry.sha256}"
            for entry in update.files
        )
        if not update.files:
            lines.append("No file changes.")
        return "\n".join(lines)

    def _prepare_locked(
        self, user_id: int, chat_id: int, archive_name: str, archive_data: bytes,
    ) -> StagedUpdate:
        if not archive_name.lower().endswith(".zip"):
            raise ValueError("The uploaded document must have a .zip filename.")
        if not archive_data or len(archive_data) > self.settings.max_upload_bytes:
            raise ValueError("The compressed ZIP is empty or exceeds MAX_UPLOAD_BYTES.")
        timeout = int(getattr(
            self.settings, "update_extract_timeout_seconds", _DEFAULT_EXTRACT_TIMEOUT_SECONDS,
        ))
        if not 1 <= timeout <= _MAX_EXTRACT_TIMEOUT_SECONDS:
            raise ValueError("UPDATE_EXTRACT_TIMEOUT_SECONDS must be between 1 and 300.")
        deadline = time.monotonic() + timeout
        entries = self._inspect_archive(archive_data, deadline)
        _check_deadline(deadline)
        files = [entry for entry in entries if not entry.is_directory]
        names = {entry.path.casefold() for entry in files}
        if "src/enan_ultra/__init__.py" not in names or "src/enan_ultra/runtime.py" not in names:
            raise ValueError("The ZIP must contain src/enan_ultra/__init__.py and runtime.py.")

        update_id = self._new_update_id()
        self._create_staging_parent()
        update_root = self.staging_parent / update_id
        release_dir = update_root / "release"
        created = False
        try:
            update_root.mkdir(mode=0o700)
            created = True
            release_dir.mkdir(mode=0o700)
            if os.name != "nt":
                update_root.chmod(0o700)
                release_dir.chmod(0o700)
            archive_path = update_root / "upload.zip"
            archive_path.write_bytes(archive_data)
            archive_path.chmod(0o600)
            _check_deadline(deadline)
            self._extract_validated(entries, release_dir, archive_data, deadline)
            _check_deadline(deadline)
            base_hashes = self._current_hashes()
            self._merge_current_files(release_dir, base_hashes, deadline)
            if self._current_hashes() != base_hashes:
                raise RuntimeError("The live project changed while the ZIP release was staged.")
            staged = self._build_manifest(
                update_id, user_id, chat_id, archive_name, archive_data, update_root,
                release_dir, archive_path, base_hashes,
                hashlib.sha256(archive_data).hexdigest(), deadline,
            )
            self._check_python_syntax(staged)
            payload = self._manifest_payload(staged)
            staged.release_hash = hashlib.sha256(self._canonical_json(payload)).hexdigest()
            staged.manifest_path.write_bytes(self._canonical_json(payload))
            staged.manifest_path.chmod(0o600)
            self.updates[update_id] = staged
            return staged
        except Exception:
            if created:
                shutil.rmtree(update_root)
            raise

    def _inspect_archive(
        self, archive_data: bytes, deadline: float | None = None,
    ) -> list[_ArchiveEntry]:
        from io import BytesIO

        _check_deadline(deadline)
        try:
            archive = zipfile.ZipFile(BytesIO(archive_data))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("The uploaded file is not a readable ZIP archive.") from exc
        with archive:
            members = archive.infolist()
            if not members:
                raise ValueError("The ZIP archive is empty.")
            if len(members) > self.settings.update_max_files:
                raise ValueError("The ZIP exceeds UPDATE_MAX_FILES.")
            seen: dict[str, tuple[str, str]] = {}
            expanded_size = 0
            validated_members: list[tuple[zipfile.ZipInfo, str, bool]] = []
            for info in members:
                _check_deadline(deadline)
                path, is_directory = self._validate_member(info)
                key = path.casefold()
                previous = seen.get(key)
                kind = "directory" if is_directory else "file"
                if previous is not None:
                    raise ValueError(f"Duplicate or case-colliding ZIP path: {path}")
                seen[key] = (path, kind)
                validated_members.append((info, path, is_directory))
                if len(path) > self.settings.update_max_path_length:
                    raise ValueError("A ZIP path exceeds UPDATE_MAX_PATH_LENGTH.")
                if path.count("/") > self.settings.update_max_depth:
                    raise ValueError("A ZIP path exceeds UPDATE_MAX_DEPTH.")
                self._validate_allowlist(path, is_directory)
                mode = (info.external_attr >> 16) & 0o170000
                expected_mode = stat.S_IFDIR if is_directory else stat.S_IFREG
                if mode not in {0, expected_mode}:
                    raise ValueError("The ZIP contains a symlink or unsupported special file.")
                if info.flag_bits & (0x1 | 0x40):
                    raise ValueError("Encrypted ZIP entries are not supported.")
                if info.compress_type not in _SUPPORTED_COMPRESSION:
                    raise ValueError("Only stored and deflate ZIP compression methods are supported.")
                if info.file_size < 0 or info.compress_size < 0:
                    raise ValueError("The ZIP contains invalid file size metadata.")
                if is_directory and info.file_size:
                    raise ValueError("A directory entry unexpectedly contains file data.")
                expanded_size += info.file_size
                if expanded_size > self.settings.max_archive_expanded_bytes:
                    raise ValueError("The ZIP exceeds MAX_ARCHIVE_EXPANDED_BYTES.")
                if info.file_size:
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > self.settings.update_max_compression_ratio:
                        raise ValueError("A ZIP entry exceeds UPDATE_MAX_COMPRESSION_RATIO.")
            self._validate_path_collisions(seen)
            verified: list[_ArchiveEntry] = []
            for info, path, is_directory in validated_members:
                _check_deadline(deadline)
                digest = hashlib.sha256()
                total = 0
                try:
                    with archive.open(info) as source:
                        total = self._hash_stream(
                            source, digest, info.file_size, deadline,
                        )
                except (
                    OSError, RuntimeError, EOFError, zlib.error,
                    zipfile.BadZipFile, NotImplementedError,
                ) as exc:
                    raise ValueError(f"ZIP entry is corrupt or unreadable: {path}") from exc
                if total != info.file_size:
                    raise ValueError(f"ZIP entry size does not match its metadata: {path}")
                verified.append(_ArchiveEntry(
                    info, path, is_directory, digest.hexdigest(),
                ))
            _check_deadline(deadline)
            return verified

    def _validate_member(self, info: zipfile.ZipInfo) -> tuple[str, bool]:
        raw = unicodedata.normalize("NFC", info.orig_filename)
        is_directory = info.is_dir()
        name = raw[:-1] if is_directory and raw.endswith("/") else raw
        if (
            not name or name.startswith(("/", "\\")) or "\\" in name
            or "\x00" in name or ":" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("The ZIP contains an absolute or malformed path.")
        path = PurePosixPath(name)
        parts = path.parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("The ZIP contains path traversal or an unsafe path component.")
        for part in parts:
            if (
                part.endswith((".", " ")) or any(ord(char) < 32 for char in part)
                or any(char in '<>:"|?*' for char in part)
            ):
                raise ValueError("The ZIP contains a non-portable or unsafe path component.")
            stem = part.split(".", 1)[0].upper()
            if stem in {"CON", "PRN", "AUX", "NUL"} or (
                len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789"
            ):
                raise ValueError("The ZIP contains a reserved Windows filename.")
        return path.as_posix(), is_directory

    def _validate_allowlist(self, path: str, is_directory: bool) -> None:
        parts = PurePosixPath(path).parts
        names = {part.casefold() for part in parts}
        basename = PurePosixPath(path).name.casefold()
        if (
            basename in _PROTECTED_NAMES
            or (basename.startswith(".env.") and basename != ".env.example")
            or basename.endswith((
                ".db", ".sqlite", ".sqlite3", ".bak", ".backup", ".pem",
                ".key", ".p12", ".pfx", ".jks", ".keystore", ".db-wal",
                ".db-shm", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal",
                ".sqlite3-shm", ".sqlite3-journal",
            ))
            or any(marker in basename for marker in ("credential", "secret"))
            or names.intersection(_PROTECTED_DIRECTORIES)
        ):
            raise ValueError(f"The ZIP attempts to include protected configuration or data: {path}")
        folded = path.casefold()
        if is_directory:
            allowed = any(
                folded == allowed_dir
                or folded.startswith(f"{allowed_dir}/")
                or allowed_dir.startswith(f"{folded}/")
                for allowed_dir in _ALLOWED_DIRECTORIES
            )
            if not allowed:
                raise ValueError(f"The ZIP contains a directory outside the update allowlist: {path}")
            return
        if folded in _ALLOWED_TOP_LEVEL_FILES:
            return
        if any(
            folded.startswith(prefix) for prefix in _ALLOWED_PREFIXES
        ):
            suffix = PurePosixPath(path).suffix.casefold()
            if folded.startswith("src/enan_ultra/") and suffix in _SOURCE_SUFFIXES:
                return
            if folded.startswith("tests/") and suffix in _TEST_SUFFIXES:
                return
            if folded.startswith("docs/") and suffix == ".md":
                return
        raise ValueError(f"The ZIP contains a file outside the update allowlist: {path}")

    @staticmethod
    def _validate_path_collisions(seen: dict[str, tuple[str, str]]) -> None:
        directory_spellings: dict[str, str] = {}
        file_keys = {key for key, (_, kind) in seen.items() if kind == "file"}
        for path, kind in seen.values():
            prefix = ""
            parts = path.split("/")
            directory_parts = parts if kind == "directory" else parts[:-1]
            for component in directory_parts:
                prefix = f"{prefix}/{component}" if prefix else component
                key = prefix.casefold()
                previous = directory_spellings.get(key)
                if previous is not None and previous != prefix:
                    raise ValueError(f"ZIP entries have a case-colliding directory path: {prefix}")
                directory_spellings[key] = prefix
                if key in file_keys:
                    raise ValueError(f"A ZIP file is also used as a parent directory: {prefix}")

    @staticmethod
    def _hash_stream(
        source: BinaryIO, digest: _HashSink, expected_size: int,
        deadline: float | None = None,
    ) -> int:
        total = 0
        _check_deadline(deadline)
        while chunk := source.read(_CHUNK_SIZE):
            _check_deadline(deadline)
            total += len(chunk)
            if total > expected_size:
                raise ValueError("ZIP entry expands beyond its declared size.")
            digest.update(chunk)
        return total

    @staticmethod
    def _extract_validated(
        entries: list[_ArchiveEntry], release_dir: Path, archive_data: bytes,
        deadline: float | None = None,
    ) -> None:
        from io import BytesIO

        _check_deadline(deadline)
        root = release_dir.resolve()
        for entry in entries:
            _check_deadline(deadline)
            target = release_dir.joinpath(*PurePosixPath(entry.path).parts).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"ZIP entry resolves outside the staging root: {entry.path}")
        with zipfile.ZipFile(BytesIO(archive_data)) as archive:
            for entry in entries:
                _check_deadline(deadline)
                target = release_dir.joinpath(*PurePosixPath(entry.path).parts)
                if entry.is_directory:
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                total = 0
                digest = hashlib.sha256()
                try:
                    with archive.open(entry.info) as source, target.open("xb") as output:
                        while chunk := source.read(_CHUNK_SIZE):
                            _check_deadline(deadline)
                            total += len(chunk)
                            if total > entry.info.file_size:
                                raise ValueError("ZIP entry expanded beyond its validated size.")
                            digest.update(chunk)
                            output.write(chunk)
                except (OSError, RuntimeError, EOFError, zlib.error, zipfile.BadZipFile) as exc:
                    raise ValueError(f"ZIP entry could not be staged safely: {entry.path}") from exc
                if total != entry.info.file_size:
                    raise ValueError(f"ZIP entry size changed during staging: {entry.path}")
                if digest.hexdigest() != entry.sha256:
                    raise ValueError(f"ZIP entry content changed during staging: {entry.path}")
                _check_deadline(deadline)
                if os.name != "nt":
                    target.chmod(0o600)
        _check_deadline(deadline)

    def _build_manifest(
        self,
        update_id: str,
        user_id: int,
        chat_id: int,
        archive_name: str,
        archive_data: bytes,
        update_root: Path,
        release_dir: Path,
        archive_path: Path,
        previous: dict[str, str],
        source_archive_sha256: str,
        deadline: float,
    ) -> StagedUpdate:
        files: list[UpdateFile] = []
        expanded_size = 0
        candidate_entries: dict[str, tuple[str, str]] = {}
        for staged_path in sorted(release_dir.rglob("*")):
            _check_deadline(deadline)
            if staged_path.is_symlink():
                raise ValueError("The merged release contains a symbolic link.")
            if staged_path.is_dir():
                continue
            if not staged_path.is_file():
                raise ValueError("The merged release contains a special file.")
            path = staged_path.relative_to(release_dir).as_posix()
            self._validate_allowlist(path, False)
            if (
                len(path) > self.settings.update_max_path_length
                or len(PurePosixPath(path).parts) - 1 > self.settings.update_max_depth
            ):
                raise ValueError("The complete candidate exceeds the configured path limits.")
            folded_path = path.casefold()
            if folded_path in candidate_entries:
                raise ValueError("The complete candidate contains duplicate or case-colliding paths.")
            candidate_entries[folded_path] = (path, "file")
            size = staged_path.stat().st_size
            expanded_size += size
            if expanded_size > self.settings.max_archive_expanded_bytes:
                raise ValueError("The complete candidate exceeds MAX_ARCHIVE_EXPANDED_BYTES.")
            old_hash = previous.get(path)
            digest = self._file_hash(staged_path)
            action = "added" if old_hash is None else "changed" if old_hash != digest else "unchanged"
            files.append(UpdateFile(path, action, size, digest, old_hash))
        self._validate_path_collisions(candidate_entries)
        if len(files) > self.settings.update_max_files:
            raise ValueError("The complete candidate exceeds UPDATE_MAX_FILES.")
        files.sort(key=lambda item: (item.action, item.path.casefold(), item.path))
        changes = tuple(
            item.path for item in files
            if item.action != "unchanged" and item.path.casefold() in {
                "pyproject.toml", "requirements.txt", "uv.lock",
            }
        )
        warnings = (
            "Production deployment requires separate explicit approval and an installed root-owned broker.",
            "Dependency changes are reported but are never installed automatically.",
            "A passing sandbox run does not validate the live systemd service or database migrations.",
            "The production broker never deploys its own approval policy or runtime control files.",
        )
        self._write_candidate_archive(release_dir, archive_path, files, deadline)
        return StagedUpdate(
            update_id=update_id,
            uploader_id=user_id,
            chat_id=chat_id,
            archive_name=archive_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:180],
            archive_size=len(archive_data),
            archive_sha256=self._file_hash(archive_path),
            source_archive_sha256=source_archive_sha256,
            archive_path=archive_path,
            release_hash="",
            release_dir=release_dir,
            manifest_path=update_root / _MANIFEST_NAME,
            files=tuple(files),
            dependency_changes=changes,
            security_warnings=warnings,
            syntax_status="NOT RUN",
            syntax_summary="Python syntax check has not run.",
        )

    def _merge_current_files(
        self, release_dir: Path, previous: dict[str, str], deadline: float,
    ) -> None:
        """Overlay the upload onto the current tree; omitted files remain present."""
        for relative, digest in sorted(previous.items()):
            _check_deadline(deadline)
            source = self.project_root.joinpath(*PurePosixPath(relative).parts)
            target = release_dir.joinpath(*PurePosixPath(relative).parts)
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file():
                    raise ValueError("An update path collides with an existing release path.")
                continue
            if source.is_symlink() or not source.is_file():
                raise RuntimeError("A current project file changed while the ZIP release was staged.")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            if self._file_hash(target) != digest:
                raise RuntimeError("A current project file changed while the ZIP release was staged.")

    def _write_candidate_archive(
        self,
        release_dir: Path,
        archive_path: Path,
        files: list[UpdateFile],
        deadline: float,
    ) -> None:
        temporary = archive_path.with_name("candidate.zip.tmp")
        temporary.unlink(missing_ok=True)
        size_limit = 20 * 1024 * 1024
        try:
            temporary.touch(mode=0o600, exist_ok=False)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
                for item in sorted(files, key=lambda value: (value.path.casefold(), value.path)):
                    _check_deadline(deadline)
                    if item.action == "removed":
                        continue
                    path = release_dir.joinpath(*PurePosixPath(item.path).parts)
                    info = zipfile.ZipInfo(item.path, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_STORED
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    digest = hashlib.sha256()
                    total = 0
                    with path.open("rb") as source, archive.open(info, "w") as output:
                        while chunk := source.read(_CHUNK_SIZE):
                            _check_deadline(deadline)
                            total += len(chunk)
                            if total > item.size:
                                raise ValueError("A staged candidate file changed while packaging.")
                            digest.update(chunk)
                            output.write(chunk)
                    if total != item.size or digest.hexdigest() != item.sha256:
                        raise ValueError("A staged candidate file changed while packaging.")
                    if temporary.stat().st_size > size_limit:
                        raise ValueError("The complete candidate exceeds the configured ZIP size limit.")
            if temporary.stat().st_size > size_limit:
                raise ValueError("The complete candidate exceeds the configured ZIP size limit.")
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, archive_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _check_python_syntax(self, update: StagedUpdate) -> None:
        errors: list[str] = []
        python_files = sorted(update.release_dir.rglob("*.py"))
        for path in python_files:
            if path.is_symlink() or not path.is_file():
                errors.append(f"Refused non-regular Python file: {path.relative_to(update.release_dir)}")
                continue
            try:
                compile(
                    path.read_bytes(), str(path.relative_to(update.release_dir)),
                    "exec", dont_inherit=True,
                )
            except (OSError, SyntaxError, UnicodeError) as exc:
                errors.append(f"{path.relative_to(update.release_dir)}: {type(exc).__name__}")
        if errors:
            update.syntax_status = "FAIL"
            update.syntax_summary = "; ".join(errors[:10])
        else:
            update.syntax_status = "PASS"
            update.syntax_summary = f"{len(python_files)} Python file(s) compiled without execution."

    def _current_hashes(self) -> dict[str, str]:
        current: dict[str, str] = {}
        candidates: list[Path] = []
        for directory in _ALLOWED_DIRECTORIES:
            root = self.project_root / PurePosixPath(directory)
            if root.exists() and root.is_dir() and not root.is_symlink():
                for current_dir, directory_names, file_names in os.walk(
                    root, followlinks=False, onerror=self._raise_walk_error,
                ):
                    directory_path = Path(current_dir)
                    directory_names[:] = [
                        name for name in directory_names
                        if (
                            not (directory_path / name).is_symlink()
                            and name.casefold() not in _PROTECTED_DIRECTORIES
                        )
                    ]
                    candidates.extend(directory_path / name for name in file_names)
        for filename in _ALLOWED_TOP_LEVEL_FILES:
            path = self.project_root / filename
            if path.is_file() and not path.is_symlink():
                candidates.append(path)
        for path in candidates:
            if path.is_symlink():
                continue
            relative = path.relative_to(self.project_root).as_posix()
            try:
                self._validate_allowlist(relative, False)
            except ValueError:
                continue
            try:
                current[relative] = self._file_hash(path)
            except OSError as exc:
                raise RuntimeError(f"Could not read current file for the update manifest: {relative}") from exc
        return current

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _raise_walk_error(error: OSError) -> None:
        raise RuntimeError("Could not enumerate the current project for the update manifest.") from error

    def _manifest_payload(self, update: StagedUpdate) -> dict[str, object]:
        return {
            "schema": 2,
            "update_id": update.update_id,
            "archive_sha256": update.archive_sha256,
            "source_archive_sha256": update.source_archive_sha256,
            "validation": {
                "syntax": update.syntax_status,
                "tests": update.test_status,
            },
            "files": [
                {
                    "path": item.path,
                    "action": item.action,
                    "size": item.size,
                    "sha256": item.sha256,
                    "previous_sha256": item.previous_sha256,
                }
                for item in update.files
            ],
        }

    def _seal_validation(self, update: StagedUpdate) -> None:
        manifest_data = self._canonical_json(self._manifest_payload(update))
        update.release_hash = hashlib.sha256(manifest_data).hexdigest()
        update.manifest_path.write_bytes(manifest_data)
        update.manifest_path.chmod(0o600)

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    def _verify_release(self, update: StagedUpdate) -> None:
        payload = self._manifest_payload(update)
        canonical_manifest = self._canonical_json(payload)
        if hashlib.sha256(canonical_manifest).hexdigest() != update.release_hash:
            raise ValueError("The staged release manifest changed; approval is blocked.")
        try:
            manifest_data = update.manifest_path.read_bytes()
            archive_hash = self._file_hash(update.archive_path)
        except OSError as exc:
            raise ValueError("The staged archive or manifest is unavailable; approval is blocked.") from exc
        if manifest_data != canonical_manifest:
            raise ValueError("The staged manifest file changed; approval is blocked.")
        if archive_hash != update.archive_sha256:
            raise ValueError("The uploaded archive changed after validation; approval is blocked.")
        expected = {
            item.path for item in update.files if item.action != "removed"
        }
        actual: set[str] = set()
        for path in update.release_dir.rglob("*"):
            if path.is_symlink():
                raise ValueError("The staged release now contains a symlink.")
            if path.is_file():
                actual.add(path.relative_to(update.release_dir).as_posix())
        if actual != expected:
            raise ValueError("The staged release file list changed; approval is blocked.")
        for item in update.files:
            if item.action == "removed":
                continue
            path = update.release_dir.joinpath(*PurePosixPath(item.path).parts)
            if self._file_hash(path) != item.sha256:
                raise ValueError("The staged release content changed; approval is blocked.")

    def _read_staged_archive(self, update: StagedUpdate) -> bytes:
        self._verify_release(update)
        return update.archive_path.read_bytes()

    def _create_staging_parent(self) -> None:
        parent = self.staging_parent
        if parent.exists() and (parent.is_symlink() or not parent.is_dir()):
            raise RuntimeError("The configured update staging path is not a private directory.")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir():
            raise RuntimeError("The configured update staging path is not a private directory.")
        if os.name != "nt":
            parent.chmod(0o700)

    def _cleanup_orphaned_releases(self) -> None:
        parent = self.staging_parent
        if not parent.exists():
            return
        if parent.is_symlink() or not parent.is_dir():
            return
        for path in parent.iterdir():
            if path.is_dir() and not path.is_symlink() and re_full_update_id(path.name):
                shutil.rmtree(path)

    def _new_update_id(self) -> str:
        while True:
            update_id = uuid.uuid4().hex[:8]
            if update_id not in self.updates and not (self.staging_parent / update_id).exists():
                return update_id

    def _authorize(self, user_id: int, chat_id: int) -> None:
        if user_id not in self.admin_ids:
            raise PermissionError("Only a configured administrator can manage ZIP updates.")
        if chat_id != self.admin_chat_id:
            raise PermissionError("ZIP update actions are restricted to ADMIN_CHAT_ID.")

    def _expire_locked(self) -> None:
        now = time.monotonic()
        for user_id, session in list(self.upload_sessions.items()):
            if session.expires_at <= now:
                self.upload_sessions.pop(user_id, None)
        for update in list(self.updates.values()):
            if update.expires_at <= now and update.state not in {"expired", "cancelled", "rejected"}:
                self._remove_update_files(update)
                update.state = "expired"
                self.updates.pop(update.update_id, None)

    @staticmethod
    def _remove_update_files(update: StagedUpdate) -> None:
        update_root = update.release_dir.parent
        if update_root.name == update.update_id and re_full_update_id(update.update_id):
            try:
                shutil.rmtree(update_root)
            except FileNotFoundError:
                return


def re_full_update_id(value: str) -> bool:
    return len(value) == 8 and all(character in "0123456789abcdef" for character in value)


def redact_secrets(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
    redacted = _TELEGRAM_TOKEN.sub("[REDACTED_TELEGRAM_TOKEN]", redacted)
    redacted = _OPENROUTER_KEY.sub("[REDACTED_API_KEY]", redacted)
    return _GENERIC_API_KEY.sub("[REDACTED_API_KEY]", redacted)
