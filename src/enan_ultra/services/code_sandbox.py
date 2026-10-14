"""Bounded archive inspection and optional isolated Python test runner."""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from enan_ultra.config import Settings

_SECRET_NAMES = {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519",
                 "authorized_keys", "dockerconfig.json", "credentials.json"}
_PATCH_SUFFIXES = {".py", ".md", ".toml", ".json", ".yaml", ".yml", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".java"}


@dataclass(frozen=True, slots=True)
class SandboxReport:
    exit_code: int
    output: str
    timed_out: bool = False


def extract_project_zip(data: bytes, destination: Path, settings: Settings) -> list[Path]:
    if len(data) > settings.max_upload_bytes:
        raise ValueError("Project archive exceeds the configured upload limit")
    root = destination.resolve()
    written: list[Path] = []
    expanded = 0
    if not zipfile.is_zipfile(__import__("io").BytesIO(data)):
        raise ValueError("Reply to a valid .zip project archive")
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Project archive is damaged") from exc
    with archive:
        members = archive.infolist()
        if len(members) > settings.sandbox_max_files:
            raise ValueError("Project archive has too many files")
        for item in members:
            name = item.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or any(part in {"..", ""} for part in path.parts) or ":" in name:
                raise ValueError("Project archive contains an unsafe path")
            mode = (item.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError("Project archive symlinks are not supported")
            if item.file_size > settings.sandbox_max_expanded_bytes - expanded:
                raise ValueError("Project archive expands beyond the configured limit")
            expanded += item.file_size
            if item.is_dir():
                continue
            if any(part.casefold() in {".git", ".venv", "venv", "node_modules", "__pycache__"} for part in path.parts):
                continue
            lowered_name = path.name.casefold()
            if (lowered_name in _SECRET_NAMES or lowered_name.startswith((".env.", "id_rsa", "id_ed25519"))
                    or "credential" in lowered_name or "secret" in lowered_name
                    or path.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"}):
                continue
            target = (root / Path(*path.parts)).resolve()
            if target != root and root not in target.parents:
                raise ValueError("Project archive path escapes its workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                actual_file_bytes = 0
                with archive.open(item) as source, target.open("xb") as output:
                    while chunk := source.read(64 * 1024):
                        actual_file_bytes += len(chunk)
                        if actual_file_bytes > item.file_size:
                            raise ValueError("Project archive size metadata is invalid")
                        output.write(chunk)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ValueError("Project archive could not be extracted safely") from exc
            target.chmod(0o600)
            written.append(target)
    if not written:
        raise ValueError("Project archive has no readable files after secret and cache exclusions")
    return written


def read_project_sources(data: bytes, settings: Settings, max_chars: int = 80_000) -> str:
    with tempfile.TemporaryDirectory(prefix="enan-code-review-") as temporary:
        root = Path(temporary)
        files = extract_project_zip(data, root, settings)
        output: list[str] = []
        total = 0
        selected = [path for path in files if path.suffix.casefold() in _PATCH_SUFFIXES]
        for path in selected[:40]:
            try:
                body = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            label = path.relative_to(root).as_posix()
            section = f"\n\n===== {label} =====\n{body}"
            if total + len(section) > max_chars:
                break
            output.append(section)
            total += len(section)
        if not output:
            raise ValueError("No supported source/text files found in the project archive")
        return "".join(output)


class CodeSandbox:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run_python_tests(self, archive_data: bytes) -> SandboxReport:
        if not self.settings.sandbox_enabled:
            raise RuntimeError("Code sandbox is disabled")
        if os.name != "posix":
            raise RuntimeError("Code sandbox is available only on the configured Linux host")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,220}", self.settings.sandbox_image):
            raise RuntimeError("Configured sandbox image name is invalid")
        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError("Rootless Docker is not installed for the bot service account")
        docker_env = {key: os.environ[key] for key in (
            "PATH", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR", "HOME",
        ) if os.environ.get(key)}
        docker_env.setdefault("PATH", "/usr/bin:/bin")
        try:
            rootless = await asyncio.create_subprocess_exec(
                docker, "info", "--format", "{{json .SecurityOptions}}",
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=docker_env,
            )
            info, _ = await asyncio.wait_for(rootless.communicate(), timeout=10)
        except TimeoutError as exc:
            if "rootless" in locals() and rootless.returncode is None:
                rootless.kill()
                await rootless.wait()
            raise RuntimeError("Rootless Docker daemon did not respond") from exc
        except OSError as exc:
            raise RuntimeError("Rootless Docker daemon is not available") from exc
        if rootless.returncode != 0 or b"rootless" not in info.lower():
            raise RuntimeError("Docker daemon must be configured in rootless mode")
        sandbox_parent = self.settings.database_path.parent / "sandbox"
        sandbox_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            sandbox_parent.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix="enan-code-test-", dir=sandbox_parent) as temporary:
            root = Path(temporary)
            extract_project_zip(archive_data, root, self.settings)
            job_name = f"enan-code-{uuid.uuid4().hex[:16]}"
            command = [
                docker, "run", "--rm", "--pull=never", "--name", job_name,
                "--network=none", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--pids-limit=128",
                "--memory", f"{self.settings.sandbox_memory_mb}m",
                "--cpus", str(self.settings.sandbox_cpu_count),
                # Rootless container UID 0 maps to the unprivileged bot service account.
                "--user", "0:0", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--tmpfs", f"/workspace:rw,nosuid,nodev,size={math.ceil(self.settings.sandbox_max_expanded_bytes / (1024 * 1024)) + 64}m",
                "--mount", f"type=bind,src={root},dst=/input,readonly",
                "--workdir", "/workspace", self.settings.sandbox_image,
                "sh", "-c", "cp -a /input/. /workspace/ && exec python -m pytest -q --disable-warnings",
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=docker_env,
                )
            except OSError as exc:
                raise RuntimeError("Rootless Docker could not start") from exc

            async def read_limited(stream: asyncio.StreamReader | None, max_bytes: int = 24_000) -> bytes:
                if stream is None:
                    return b""
                collected = bytearray()
                while chunk := await stream.read(8192):
                    if len(collected) < max_bytes:
                        collected.extend(chunk[:max_bytes - len(collected)])
                return bytes(collected)

            readers = [asyncio.create_task(read_limited(process.stdout)), asyncio.create_task(read_limited(process.stderr))]
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=self.settings.sandbox_timeout_seconds)
            except TimeoutError:
                timed_out = True
                killer = await asyncio.create_subprocess_exec(
                    docker, "kill", job_name, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL, env=docker_env,
                )
                await asyncio.wait_for(killer.wait(), timeout=5)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            stdout, stderr = await asyncio.gather(*readers)
            text = (stdout + (b"\n" if stdout and stderr else b"") + stderr).decode("utf-8", "replace")
            if timed_out:
                text += "\nSandbox time limit reached; the container was stopped."
            return SandboxReport(process.returncode or 0, text[-24_000:], timed_out)
