"""Create or restore a consistent SQLite backup without exporting user content."""

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


def configured_database() -> Path:
    values = dotenv_values()
    configured = os.environ.get("DATABASE_PATH") or str(values.get("DATABASE_PATH") or "data/telegram_ai.sqlite3")
    return Path(configured)


def _copy_database(source: Path, destination: Path, *, replace: bool) -> None:
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file() or source == destination:
        raise ValueError("Source must be an existing database file distinct from the destination")
    if destination.exists() and not replace:
        raise FileExistsError("Destination exists; pass --replace to overwrite it")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        destination.parent.chmod(0o700)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="make an atomic SQLite backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore", help="restore a backup to an explicit database path")
    restore.add_argument("source", type=Path)
    restore.add_argument("--to", type=Path, required=True)
    restore.add_argument("--replace", action="store_true", help="preserve the current DB, then replace it")
    args = parser.parse_args()
    try:
        database = configured_database()
        if args.command == "backup":
            _copy_database(database, args.destination, replace=True)
            print(f"Database backup created: {args.destination}")
            print("Keep the matching APP_DATA_KEY in a separate secure backup.")
            return 0
        if args.to.resolve() == database.resolve() and _service_is_active():
            raise RuntimeError("Stop telegram-ai.service before restoring its live database")
        if args.to.exists() and not args.replace:
            raise FileExistsError("Destination exists; pass --replace to preserve it and restore")
        if args.to.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            preserved = args.to.with_name(f"{args.to.name}.pre-restore-{stamp}")
            _copy_database(args.to, preserved, replace=False)
        if args.to.resolve() == database.resolve():
            Path(f"{args.to}-wal").unlink(missing_ok=True)
            Path(f"{args.to}-shm").unlink(missing_ok=True)
        _copy_database(args.source, args.to, replace=args.replace)
        print(f"Database restored to {args.to}; use the same APP_DATA_KEY to decrypt Personal KB data.")
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Database backup/restore failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
