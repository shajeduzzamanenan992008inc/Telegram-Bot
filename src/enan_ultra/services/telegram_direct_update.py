"""Safely overlay an administrator-provided ZIP onto the running project."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


UPDATE_ARCHIVE_PATH = Path("/tmp/bot_update.zip")

_PROTECTED_ROOT_DIRS = frozenset({
    ".git", ".telegram-zip-updates", "backups", "data", "instance", "logs",
    "media", "storage", "uploads", "user_data", "user-data",
})
_PROTECTED_DIRS_ANYWHERE = frozenset({
    "__pycache__", ".pytest_cache", ".ruff_cache",
})


def _is_protected(relative_path: PurePosixPath, project_root: Path, settings: Any) -> bool:
    parts = tuple(part.casefold() for part in relative_path.parts)
    if not parts:
        return True
    if parts[0] in _PROTECTED_ROOT_DIRS or parts[0] in {".venv", "venv", "env"}:
        return True
    if any(part in _PROTECTED_DIRS_ANYWHERE for part in parts):
        return True
    basename = parts[-1]
    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename == "stickers_store.db" or basename.endswith((
        ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal",
        ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm",
    )):
        return True

    configured_database = Path(settings.database_path)
    if not configured_database.is_absolute():
        configured_database = project_root / configured_database
    try:
        database_relative = (
            configured_database.resolve().relative_to(project_root).as_posix().casefold()
        )
    except ValueError:
        database_relative = ""
    archive_relative = relative_path.as_posix().casefold()
    if database_relative:
        sidecars = {
            database_relative,
            *(database_relative + suffix for suffix in ("-wal", "-shm", "-journal")),
        }
        if archive_relative in sidecars:
            return True
        database_parent = database_relative.rpartition("/")[0]
        if database_parent and archive_relative.startswith(database_parent + "/"):
            return True
    return False


def _safe_member_path(info: zipfile.ZipInfo, max_depth: int, max_length: int) -> PurePosixPath:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        raise ValueError("ZIP contains an invalid file path")
    path = PurePosixPath(name)
    if (
        path.is_absolute() or any(part in {"..", ""} for part in path.parts)
        or any(":" in part for part in path.parts)
        or len(name) > max_length or len(path.parts) > max_depth
    ):
        raise ValueError("ZIP contains an unsafe or overlong file path")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG}:
        raise ValueError("ZIP contains a symbolic link or special file")
    if info.is_dir() and kind not in {0, stat.S_IFDIR}:
        raise ValueError("ZIP contains an invalid directory entry")
    if not info.is_dir() and kind == stat.S_IFDIR:
        raise ValueError("ZIP contains an invalid file entry")
    if info.flag_bits & 0x1:
        raise ValueError("Encrypted ZIP entries are not supported")
    return path


def extract_update_archive(
    archive_path: Path,
    project_root: Path,
    settings: Any,
) -> list[str]:
    """Validate and atomically replace included files, rolling back on write errors."""
    root = project_root.resolve(strict=True)
    if not zipfile.is_zipfile(archive_path):
        raise ValueError("The uploaded file is not a valid ZIP archive")

    max_files = int(settings.update_max_files)
    max_depth = int(settings.update_max_depth)
    max_path_length = int(settings.update_max_path_length)
    max_expanded = int(settings.max_archive_expanded_bytes)
    max_ratio = int(settings.update_max_compression_ratio)
    planned: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen_paths: set[str] = set()
    expanded = 0

    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > max_files * 2:
            raise ValueError("ZIP contains too many entries")
        for info in infos:
            relative = _safe_member_path(info, max_depth, max_path_length)
            normalized = relative.as_posix().casefold()
            if normalized in seen_paths:
                raise ValueError("ZIP contains duplicate or case-colliding paths")
            seen_paths.add(normalized)
            if info.is_dir():
                continue
            if info.file_size < 0 or info.compress_size < 0:
                raise ValueError("ZIP contains invalid size metadata")
            expanded += info.file_size
            if expanded > max_expanded:
                raise ValueError("ZIP exceeds the configured expanded size limit")
            if info.file_size and (
                not info.compress_size or info.file_size / info.compress_size > max_ratio
            ):
                raise ValueError("ZIP entry exceeds the configured compression ratio")
            if not _is_protected(relative, root, settings):
                planned.append((info, relative))

        if not planned:
            raise ValueError("ZIP does not contain any files that can be updated")
        if len(planned) > max_files:
            raise ValueError("ZIP contains too many update files")

        for info, relative in planned:
            target = root.joinpath(*relative.parts)
            cursor = root
            for part in relative.parts[:-1]:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("An update path crosses a symbolic link")
            if target.is_symlink():
                raise ValueError("An update target is a symbolic link")
            if target.exists() and not target.is_file():
                raise ValueError("An update target conflicts with an existing directory")
            parent = target.parent
            while parent != root and not parent.exists():
                parent = parent.parent
            if parent.exists() and not parent.is_dir():
                raise ValueError("An update path conflicts with an existing file")

        with tempfile.TemporaryDirectory(prefix=".bot_update_extract_", dir=root) as temp_name:
            workspace = Path(temp_name)
            stage_root = workspace / "content"
            stage_root.mkdir()
            backup_root = workspace / "backup"
            staged: list[tuple[Path, Path, PurePosixPath]] = []
            for info, relative in planned:
                staged_path = stage_root.joinpath(*relative.parts)
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, staged_path.open("xb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                staged_mode = info.external_attr >> 16
                if staged_mode:
                    safe_mode = 0o755 if staged_mode & 0o111 else 0o644
                    os.chmod(staged_path, safe_mode)
                staged.append((staged_path, root.joinpath(*relative.parts), relative))

            applied: list[tuple[Path, Path | None]] = []
            try:
                for staged_path, target, relative in staged:
                    cursor = root
                    for part in relative.parts[:-1]:
                        cursor = cursor / part
                        if cursor.is_symlink():
                            raise ValueError("An update path crosses a symbolic link")
                    if target.is_symlink():
                        raise ValueError("An update target is a symbolic link")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup: Path | None = None
                    target_mode: int | None = None
                    if target.exists():
                        target_mode = stat.S_IMODE(target.stat().st_mode)
                        backup = backup_root.joinpath(*relative.parts)
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup)
                    if target_mode is not None:
                        os.chmod(staged_path, target_mode)
                    os.replace(staged_path, target)
                    applied.append((target, backup))
            except Exception as exc:
                rollback_errors: list[str] = []
                for target, backup in reversed(applied):
                    try:
                        if target.exists() or target.is_symlink():
                            target.unlink()
                        if backup is not None and backup.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(backup, target)
                    except OSError as rollback_exc:
                        rollback_errors.append(type(rollback_exc).__name__)
                if rollback_errors:
                    raise RuntimeError(
                        "Update failed and rollback was incomplete: " + ", ".join(rollback_errors)
                    ) from exc
                raise

    return [relative.as_posix() for _, relative in planned]
