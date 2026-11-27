"""Create encrypted SQLite backups and restore verified snapshots."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values

from enan_ultra.crypto import DataCipher


def configured_database() -> Path:
    values = _environment_values()
    configured = os.environ.get("DATABASE_PATH") or str(values.get("DATABASE_PATH") or "data/telegram_ai.sqlite3")
    return Path(configured)


def _environment_values() -> dict[str, str | None]:
    configured_file = Path("/etc/telegram-ai/bot.env")
    return dotenv_values(configured_file if configured_file.is_file() else None)


def _copy_database(source: Path, destination: Path, *, replace: bool) -> None:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file() or source == destination:
        raise ValueError("Source must be an existing database file distinct from the destination")
    if destination.exists() and not replace:
        raise FileExistsError("Destination exists; pass --replace to overwrite it")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        destination.parent.chmod(0o700)
    existing_stat = destination.stat() if destination.exists() else None
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
    if os.name != "nt":
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    source_db = target_db = None
    try:
        source_db = sqlite3.connect(source)
        target_db = sqlite3.connect(temporary, check_same_thread=False)
        source_db.backup(target_db)
        integrity = target_db.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        target_db.close()
        target_db = None
        source_db.close()
        source_db = None
        if os.name != "nt":
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
            if existing_stat:
                os.chown(temporary, existing_stat.st_uid, existing_stat.st_gid)
                temporary.chmod(stat.S_IMODE(existing_stat.st_mode))
        os.replace(temporary, destination)
    finally:
        if target_db:
            target_db.close()
        if source_db:
            source_db.close()
        temporary.unlink(missing_ok=True)


def _service_is_active() -> bool:
    systemctl = shutil.which("systemctl")
    if os.name != "posix" or not systemctl:
        return False
    try:
        result = subprocess.run(
            [systemctl, "is-active", "--quiet", "telegram-ai.service"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _backup_cipher() -> DataCipher:
    values = _environment_values()
    key = os.environ.get("APP_DATA_KEY") or str(values.get("APP_DATA_KEY") or "")
    if len(key) < 32 or key.startswith("replace-"):
        raise ValueError("APP_DATA_KEY is required to encrypt or decrypt database backups")
    return DataCipher(key)


def _encrypted_backup(database: Path, destination: Path, cipher: DataCipher) -> None:
    if database.resolve() == destination.resolve():
        raise ValueError("Backup destination must differ from the live database")
    database_stat = database.stat()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        destination.parent.chmod(0o700)
        os.chown(destination.parent, database_stat.st_uid, database_stat.st_gid)
    with tempfile.TemporaryDirectory(prefix="telegram-ai-backup-", dir=destination.parent) as directory:
        plain = Path(directory) / "snapshot.sqlite3"
        _copy_database(database, plain, replace=False)
        payload = cipher.encrypt(plain.read_bytes())
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False,
        ) as handle:
            encrypted = Path(handle.name)
            if os.name != "nt":
                encrypted.chmod(stat.S_IRUSR | stat.S_IWUSR)
                os.chown(encrypted, database_stat.st_uid, database_stat.st_gid)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(encrypted, destination)
        finally:
            encrypted.unlink(missing_ok=True)


def _decrypted_snapshot(source: Path, cipher: DataCipher) -> Path:
    payload = source.read_bytes()
    try:
        plaintext = cipher.decrypt(payload)
    except ValueError as exc:
        raise ValueError("Backup decryption failed; verify APP_DATA_KEY and backup integrity") from exc
    if not plaintext.startswith(b"SQLite format 3\x00"):
        raise ValueError("Decrypted backup is not a SQLite database")
    handle = tempfile.NamedTemporaryFile(prefix="telegram-ai-restore-", suffix=".sqlite3", delete=False)
    path = Path(handle.name)
    try:
        if os.name != "nt":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        handle.write(plaintext)
        handle.close()
        return path
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="make an atomic SQLite backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore", help="restore a backup to an explicit database path")
    restore.add_argument("source", type=Path)
    restore.add_argument("--to", type=Path, required=True)
    restore.add_argument("--replace", action="store_true", help="preserve the current DB, then replace it")
    restore.add_argument(
        "--allow-unencrypted", action="store_true",
        help="explicitly allow restoring a legacy plaintext SQLite backup",
    )
    args = parser.parse_args()
    try:
        database = configured_database()
        if args.command == "backup":
            _encrypted_backup(database, args.destination, _backup_cipher())
            print(f"Encrypted database backup created: {args.destination}")
            return 0
        source = args.source.resolve()
        destination = args.to.resolve()
        if source == destination:
            raise ValueError("Restore source and destination must differ")
        temporary: Path | None = None
        with source.open("rb") as handle:
            prefix = handle.read(16)
        if prefix.startswith(b"SQLite format 3\x00"):
            if not args.allow_unencrypted:
                raise ValueError("Plaintext backup refused; pass --allow-unencrypted only for a trusted legacy backup")
        else:
            temporary = _decrypted_snapshot(source, _backup_cipher())
            source = temporary
        try:
            probe = sqlite3.connect(source)
            try:
                integrity = probe.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError("Restore source failed SQLite integrity_check")
            finally:
                probe.close()
            if destination == database.resolve() and _service_is_active():
                raise RuntimeError("Stop telegram-ai.service before restoring its live database")
            if destination.exists() and not args.replace:
                raise FileExistsError("Destination exists; pass --replace to preserve it and restore")
            if destination.exists():
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                preserved = destination.with_name(f"{destination.name}.pre-restore-{stamp}")
                _copy_database(destination, preserved, replace=False)
            if destination == database.resolve():
                Path(f"{destination}-wal").unlink(missing_ok=True)
                Path(f"{destination}-shm").unlink(missing_ok=True)
            _copy_database(source, args.to, replace=args.replace)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        print(f"Database restored to {args.to}; APP_DATA_KEY must remain unchanged.")
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Database backup/restore failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
