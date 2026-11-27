#!/usr/bin/env python3
"""Verify a live Telegram-Bot deployment in one command.

Run this on the target host after ``deploy/install_ubuntu.sh`` has been executed:

    sudo python3 /opt/enan-ultra/current/scripts/smoke_deployment.py

It is read-only: it never sends user messages, never calls an AI provider, and
never restarts a service. Everything it needs is in the standard library.
Exit code 0 means every applicable check passed; 1 means at least one failed;
2 means the arguments themselves were unusable.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_ENV_FILE = "/etc/telegram-ai/bot.env"
DEFAULT_SERVICE = "telegram-ai"
DEFAULT_DATABASE = "/var/lib/telegram-ai/telegram_ai.sqlite3"
DEFAULT_CLAMAV_SOCKET = "/var/run/clamav/clamd.ctl"
DEFAULT_MINI_APP_PORT = 8788
REQUIRED_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "ADMIN_USER_IDS",
    "APP_DATA_KEY",
    "DASHBOARD_AUTH_TOKEN",
)
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
MINIMUM_FREE_BYTES = 512 * 1024 * 1024


@dataclass
class Result:
    """One verification step."""

    name: str
    status: str
    detail: str


class Report:
    """Collect results and print them as a table."""

    def __init__(self) -> None:
        self.results: list[Result] = []

    def add(self, name: str, status: str, detail: str) -> None:
        self.results.append(Result(name, status, detail))

    def passed(self, name: str, detail: str) -> None:
        self.add(name, PASS, detail)

    def failed(self, name: str, detail: str) -> None:
        self.add(name, FAIL, detail)

    def skipped(self, name: str, detail: str) -> None:
        self.add(name, SKIP, detail)

    @property
    def failures(self) -> list[Result]:
        return [item for item in self.results if item.status == FAIL]

    def render(self) -> str:
        width = max((len(item.name) for item in self.results), default=4)
        lines = [f"{'CHECK'.ljust(width)}  STATUS  DETAIL"]
        for item in self.results:
            lines.append(f"{item.name.ljust(width)}  {item.status:<6}  {item.detail}")
        return "\n".join(lines)


def parse_env_file(path: Path, report: Report) -> dict[str, str]:
    """Read KEY=VALUE pairs without printing any value."""
    values: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.failed("environment file", f"cannot read {path}: {exc.strerror or exc}")
        return values
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    report.passed("environment file", f"{path} parsed ({len(values)} keys)")
    return values


def check_environment_permissions(path: Path, report: Report) -> None:
    try:
        info = path.stat()
    except OSError as exc:
        report.failed("env permissions", f"cannot stat {path}: {exc.strerror or exc}")
        return
    mode = stat.S_IMODE(info.st_mode)
    problems = []
    if mode & 0o077:
        problems.append(f"mode {oct(mode)} exposes the file to group/other")
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0 and info.st_uid != 0:
        problems.append(f"owned by uid {info.st_uid}, expected root")
    if problems:
        report.failed("env permissions", "; ".join(problems))
    else:
        report.passed("env permissions", f"mode {oct(mode)}, owner uid {info.st_uid}")


def check_required_keys(env: dict[str, str], report: Report) -> None:
    missing = [key for key in REQUIRED_KEYS if not env.get(key, "").strip()]
    if missing:
        report.failed("required keys", "missing or empty: " + ", ".join(missing))
    else:
        report.passed("required keys", f"{len(REQUIRED_KEYS)} required keys present (values hidden)")


def check_database_path(env: dict[str, str], report: Report) -> Path | None:
    """Validate the POSIX state path; ``None`` means the dependent checks cannot run."""
    raw = env.get("DATABASE_PATH", DEFAULT_DATABASE).strip() or DEFAULT_DATABASE
    path = PurePosixPath(raw)
    if not path.is_absolute():
        report.failed("database path", f"must be absolute, got {raw!r}")
        return None
    if not str(path).startswith("/var/lib/telegram-ai/"):
        report.failed("database path", f"{path} is outside the systemd writable state root")
        return None
    report.passed("database path", str(path))
    return Path(str(path))


def check_service(service: str, report: Report) -> str:
    """Return the service MainPID, or an empty string when it is not running."""
    try:
        completed = subprocess.run(
            [
                "systemctl",
                "show",
                service,
                "--property=ActiveState,SubState,MainPID,NRestarts,ExecMainStartTimestamp",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        report.failed("systemd service", f"systemctl unavailable: {exc}")
        return ""
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        report.failed("systemd service", detail[0] if detail else f"exit {completed.returncode}")
        return ""
    properties = {}
    for line in completed.stdout.splitlines():
        key, _, value = line.partition("=")
        properties[key.strip()] = value.strip()
    active = properties.get("ActiveState", "unknown")
    pid = properties.get("MainPID", "0")
    if active != "active" or pid in {"", "0"}:
        report.failed("systemd service", f"{service} state={active} MainPID={pid or '0'}")
        return ""
    restarts = properties.get("NRestarts", "0")
    report.passed(
        "systemd service",
        f"{service} active (PID {pid}, restarts {restarts})",
    )
    return pid


def check_health_marker(
    database_path: Path,
    main_pid: str,
    update_deployment_enabled: bool,
    report: Report,
) -> int | None:
    """Validate the PID-bound runtime health marker written at startup."""
    marker_path = database_path.parent / "runtime-health.json"
    if not update_deployment_enabled:
        report.skipped(
            "health marker",
            "UPDATE_DEPLOYMENT_ENABLED is false, so the marker is not written "
            "(enable it to make the broker's health gate verifiable)",
        )
        return None
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except OSError as exc:
        report.failed("health marker", f"cannot read {marker_path}: {exc.strerror or exc}")
        return None
    except json.JSONDecodeError as exc:
        report.failed("health marker", f"{marker_path} is not valid JSON: {exc.msg}")
        return None
    problems = []
    marker_pid = payload.get("pid")
    if main_pid and str(marker_pid) != main_pid:
        problems.append(f"pid {marker_pid} does not match service MainPID {main_pid}")
    if payload.get("database_open") is not True:
        problems.append("database_open is not true")
    bot_id = payload.get("telegram_bot_id")
    if not isinstance(bot_id, int) or isinstance(bot_id, bool):
        problems.append("telegram_bot_id is missing")
        bot_id = None
    started_at = payload.get("started_at")
    try:
        started = float(started_at)
    except (TypeError, ValueError):
        problems.append("started_at is not a timestamp")
        started = 0.0
    if started > 0 and started > time.time() + 60:
        problems.append("started_at is in the future (clock skew)")
    origin = str(payload.get("import_origin", ""))
    if not origin.endswith(("src/enan_ultra/runtime.py", "src\\enan_ultra\\runtime.py")):
        problems.append(f"import_origin does not point at the release tree: {origin!r}")
    if problems:
        report.failed("health marker", "; ".join(problems))
    else:
        age = max(0.0, time.time() - started)
        report.passed(
            "health marker",
            f"PID {marker_pid}, bot id {bot_id}, written {age / 60:.1f} min ago",
        )
    return bot_id if isinstance(bot_id, int) else None


def check_database(path: Path, report: Report) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        report.failed("database", f"cannot open {path}: {exc}")
        return
    try:
        cursor = connection.execute("SELECT 1")
        cursor.fetchone()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if not quick_check or quick_check[0] != "ok":
            report.failed("database", f"PRAGMA quick_check returned {quick_check!r}")
            return
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()
        report.passed("database", f"read-only query ok, {tables[0] if tables else 0} tables")
    except sqlite3.Error as exc:
        report.failed("database", f"query failed: {exc}")
    finally:
        connection.close()


def check_free_space(path: Path, report: Report) -> None:
    target = path.parent if path.parent.exists() else Path("/")
    try:
        usage = os.statvfs(target)
    except (AttributeError, OSError) as exc:
        report.skipped("state disk", f"cannot read filesystem usage: {exc}")
        return
    free_bytes = usage.f_bavail * usage.f_frsize
    total_bytes = usage.f_blocks * usage.f_frsize
    if free_bytes < MINIMUM_FREE_BYTES:
        report.failed("state disk", f"{free_bytes // (1024 * 1024)} MiB free on {target}")
    else:
        percent = (free_bytes / total_bytes * 100) if total_bytes else 0.0
        report.passed("state disk", f"{percent:.0f}% free on {target}")


def check_telegram(env: dict[str, str], expected_bot_id: int | None, timeout: float, report: Report) -> None:
    """Call getMe: no chat is contacted and no message is sent."""
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        report.failed("telegram getMe", "TELEGRAM_BOT_TOKEN is empty")
        return
    url = f"https://api.telegram.org/bot{token}/getMe"
    if env.get("LOCAL_BOT_API_BASE_URL", "").strip():
        report.skipped("telegram getMe", "LOCAL_BOT_API_BASE_URL is set; check the local Bot API server yourself")
        return
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed Telegram API host
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        report.failed("telegram getMe", f"Telegram rejected the token (HTTP {exc.code})")
        return
    except (urllib.error.URLError, OSError, ValueError) as exc:
        report.failed("telegram getMe", f"request failed: {exc}")
        return
    if not payload.get("ok"):
        report.failed("telegram getMe", f"Telegram returned ok=false: {payload.get('description')}")
        return
    result = payload.get("result") or {}
    bot_id = result.get("id")
    username = result.get("username", "unknown")
    if expected_bot_id is not None and bot_id != expected_bot_id:
        report.failed(
            "telegram getMe",
            f"live bot id {bot_id} does not match the health marker id {expected_bot_id}",
        )
        return
    report.passed("telegram getMe", f"@{username} (id {bot_id})")


def check_clamav(env: dict[str, str], report: Report) -> None:
    enabled = env.get("MALWARE_SCAN_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        report.skipped("clamav socket", "MALWARE_SCAN_ENABLED is not true")
        return
    socket_path = env.get("CLAMAV_SOCKET_PATH", "").strip() or DEFAULT_CLAMAV_SOCKET
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(socket_path)
            client.sendall(b"zPING\0")
            reply = client.recv(64)
    except OSError as exc:
        report.failed("clamav socket", f"{socket_path}: {exc.strerror or exc}")
        return
    if reply.strip(b"\0\n") != b"PONG":
        report.failed("clamav socket", f"unexpected reply {reply[:32]!r}")
        return
    report.passed("clamav socket", f"{socket_path} answered PONG")


def check_mini_app(env: dict[str, str], timeout: float, report: Report) -> None:
    url = env.get("MINI_APP_URL", "").strip()
    if not url:
        report.skipped("mini app healthz", "MINI_APP_URL is not configured")
        return
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        report.failed("mini app healthz", f"MINI_APP_URL has no host: {url!r}")
        return
    try:
        port = int(env.get("MINI_APP_PORT", "") or DEFAULT_MINI_APP_PORT)
    except ValueError:
        report.failed("mini app healthz", "MINI_APP_PORT is not an integer")
        return
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(
            "GET",
            "/healthz",
            headers={"Host": parsed.hostname, "X-Forwarded-Proto": "https"},
        )
        response = connection.getresponse()
        body = response.read(512).decode("utf-8", "replace")
    except OSError as exc:
        report.failed("mini app healthz", f"127.0.0.1:{port}: {exc}")
        return
    finally:
        connection.close()
    if response.status != 200:
        report.failed(
            "mini app healthz",
            f"HTTP {response.status} from 127.0.0.1:{port} (421 means Host/HTTPS headers did not match)",
        )
        return
    try:
        payload = json.loads(body)
    except ValueError:
        report.failed("mini app healthz", f"non-JSON response: {body[:80]!r}")
        return
    if payload.get("status") != "ok":
        report.failed("mini app healthz", f"unexpected payload {payload!r}")
        return
    report.passed("mini app healthz", f"{parsed.hostname} via 127.0.0.1:{port}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only live verification of a deployed Telegram-Bot service.",
    )
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="systemd EnvironmentFile (default: %(default)s)")
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="systemd unit name (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=15.0, help="network timeout in seconds (default: 15)")
    parser.add_argument("--skip-telegram", action="store_true", help="skip the Telegram getMe check")
    parser.add_argument("--skip-clamav", action="store_true", help="skip the ClamAV socket check")
    parser.add_argument("--skip-mini-app", action="store_true", help="skip the Mini App health check")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    return parser


def run(args: argparse.Namespace) -> int:
    report = Report()
    env_path = Path(args.env_file)
    env = parse_env_file(env_path, report)
    check_environment_permissions(env_path, report)
    check_required_keys(env, report)
    database_path = check_database_path(env, report)
    main_pid = check_service(args.service, report)
    update_enabled = env.get("UPDATE_DEPLOYMENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    bot_id = None
    if database_path is None:
        reason = "skipped because DATABASE_PATH is not a valid state path"
        report.skipped("health marker", reason)
        report.skipped("database", reason)
        report.skipped("state disk", reason)
    else:
        bot_id = check_health_marker(database_path, main_pid, update_enabled, report)
        check_database(database_path, report)
        check_free_space(database_path, report)
    if args.skip_telegram:
        report.skipped("telegram getMe", "skipped by --skip-telegram")
    else:
        check_telegram(env, bot_id, args.timeout, report)
    if args.skip_clamav:
        report.skipped("clamav socket", "skipped by --skip-clamav")
    else:
        check_clamav(env, report)
    if args.skip_mini_app:
        report.skipped("mini app healthz", "skipped by --skip-mini-app")
    else:
        check_mini_app(env, args.timeout, report)

    if args.json:
        print(json.dumps([result.__dict__ for result in report.results], indent=2))
    else:
        print(report.render())
        print()
    summary = (
        f"{len(report.results) - len(report.failures)}/{len(report.results)} checks passed"
    )
    if report.failures:
        if not args.json:
            print(f"FAILED: {summary}. Fix the failures above before trusting this host.")
        return 1
    if not args.json:
        print(f"OK: {summary}. Manual Telegram checks are listed in docs/LIVE_DEPLOY_VERIFICATION.md.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.timeout <= 0:
        print("--timeout must be positive", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
