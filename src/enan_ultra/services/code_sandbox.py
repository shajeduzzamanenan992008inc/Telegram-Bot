"""Bounded archive inspection and optional isolated Python test runner."""

from __future__ import annotations

import asyncio
import io
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
_PROTECTED_PATCH_PREFIXES = (
    ".github/workflows/", "deploy/", "src/enan_ultra/security.py",
    "src/enan_ultra/actions.py", "src/enan_ultra/runtime.py",
    "src/enan_ultra/config.py", "src/enan_ultra/handlers.py",
    "src/enan_ultra/services/code_sandbox.py",
    "src/enan_ultra/services/mission_control.py",
    "src/enan_ultra/storage/",
    "pyproject.toml", "requirements.txt", "uv.lock",
)


@dataclass(frozen=True, slots=True)
class SandboxReport:
    exit_code: int
    output: str
    timed_out: bool = False
    candidate_archive: bytes | None = None


def extract_project_zip(
    data: bytes, destination: Path, settings: Settings, *, max_files: int | None = None,
) -> list[Path]:
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
        if len(members) > (
            settings.sandbox_max_files if max_files is None else max_files
        ):
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


def sanitize_project_archive(data: bytes, settings: Settings) -> bytes:
    """Repack only files accepted by the bounded extractor, excluding secrets/caches."""
    with tempfile.TemporaryDirectory(prefix="enan-evolution-source-") as temporary:
        root = Path(temporary) / "source"
        root.mkdir(mode=0o700)
        files = extract_project_zip(data, root, settings)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                name = path.relative_to(root).as_posix()
                archive.writestr(name, path.read_bytes())
        return output.getvalue()


def validate_complete_project_archive(data: bytes, settings: Settings) -> None:
    """Validate an export without allowing the bounded extractor to silently omit files."""
    with tempfile.TemporaryDirectory(prefix="enan-evolution-verify-") as temporary:
        root = Path(temporary)
        extracted = extract_project_zip(data, root, settings)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive_files = {
                item.filename.replace("\\", "/").rstrip("/")
                for item in archive.infolist() if not item.is_dir()
            }
        extracted_files = {path.relative_to(root).as_posix() for path in extracted}
        if archive_files != extracted_files:
            raise ValueError("Archive contains excluded or unsupported files")


def validate_evolution_patch(patch: bytes) -> str:
    """Validate a bounded text diff and reject paths that need enhanced manual review."""
    if not patch or len(patch) > 1_000_000:
        raise ValueError("Patch must be between 1 byte and 1 MB")
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Patch must be UTF-8 text") from exc
    if "GIT binary patch" in text or "\x00" in text:
        raise ValueError("Binary patches are not supported")
    if _KNOWN_SECRET.search(text) or any(
        len(match.group(1)) >= 20
        and not match.group(1).casefold().startswith(("replace-", "your-", "example-", "<"))
        for match in _CREDENTIAL_LITERAL.finditer(text)
    ):
        raise ValueError("Patch appears to contain a credential; automated review is blocked")
    paths: list[str | None] = []
    for line in text.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        raw = line[4:].split("\t", 1)[0].strip()
        if raw == "/dev/null":
            paths.append(None)
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        normalized = raw.replace("\\", "/")
        parts = normalized.split("/")
        if (
            not normalized or normalized.startswith("/") or ":" in parts[0]
            or any(part in {"", ".", "..", ".git"} for part in parts)
        ):
            raise ValueError("Patch contains an unsafe repository path")
        folded = normalized.casefold()
        filename = parts[-1].casefold()
        suffix = Path(filename).suffix.casefold()
        if (
            filename in {".env", ".npmrc", ".pypirc", ".netrc"}
            or filename.startswith((".env.", "id_rsa", "id_ed25519"))
            or any(
                part.casefold() in {"secrets", "credentials"}
                or "secret" in part.casefold() or "credential" in part.casefold()
                for part in parts
            )
            or suffix in {".pem", ".key", ".p12", ".pfx"}
            or any(folded == prefix or folded.startswith(prefix) for prefix in _PROTECTED_PATCH_PREFIXES)
        ):
            raise ValueError("Patch touches a protected security, configuration, storage, or deployment path")
        paths.append(normalized)
    unique_paths = {path for path in paths if path is not None}
    if not paths or len(paths) % 2 or len(unique_paths) > 20:
        raise ValueError("Patch must contain valid file headers for at most 20 files")
    if any("new file mode 120000" in line or "old mode 120000" in line for line in text.splitlines()):
        raise ValueError("Symlink changes are not supported")
    for index in range(0, len(paths), 2):
        old, new = paths[index:index + 2]
        if old is None and new is None:
            raise ValueError("Patch file headers are invalid")
        if old is not None and new is not None and old != new:
            raise ValueError("Renaming files is not supported")
    return text


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(\b)\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
)
_CREDENTIAL_LITERAL = re.compile(
    r"(?i)(?:api[_ -]?key|token|secret|password)\s*[:=]\s*['\"]([^'\"]{16,})['\"]",
)
_KNOWN_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"\d{6,12}:[A-Za-z0-9_-]{25,})\b",
)


def redact_sandbox_output(value: str) -> str:
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(r"\1[REDACTED]", value)
    return value[-24_000:]


class CodeSandbox:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run_python_tests(self, archive_data: bytes) -> SandboxReport:
        return await self._run_python_tests(archive_data)

    async def run_update_tests(self, archive_data: bytes) -> SandboxReport:
        """Test the complete merged update candidate under update-specific bounds."""
        return await self._run_python_tests(
            archive_data,
            max_files=self.settings.update_max_files,
            timeout_seconds=self.settings.update_test_timeout_seconds,
        )

    async def run_python_patch_tests(
        self, archive_data: bytes, patch_data: bytes, *, include_candidate: bool = False,
    ) -> SandboxReport:
        patch_text = validate_evolution_patch(patch_data)
        return await self._run_python_tests(
            archive_data, patch_text.encode("utf-8"), include_candidate=include_candidate,
        )

    async def _run_python_tests(
        self, archive_data: bytes, patch_data: bytes | None = None, *,
        max_files: int | None = None, timeout_seconds: int | None = None,
        include_candidate: bool = False,
    ) -> SandboxReport:
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
            temporary_root = Path(temporary)
            root = temporary_root / "source"
            root.mkdir(mode=0o700)
            extract_project_zip(
                archive_data, root, self.settings, max_files=max_files,
            )
            patch_path: Path | None = None
            if patch_data is not None:
                patch_path = temporary_root / "candidate.patch"
                patch_path.write_bytes(patch_data)
                patch_path.chmod(0o600)
            job_name = f"enan-code-{uuid.uuid4().hex[:16]}"
            test_command = "cp -a /input/. /workspace/ && exec python -m pytest -q --disable-warnings"
            if patch_path is not None:
                test_command = (
                    "cp -a /input/. /workspace/ && git apply --check /candidate.patch && "
                    "git apply /candidate.patch && python -m compileall -q /workspace && "
                    "exec python -m pytest -q --disable-warnings"
                )
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
                *( ["--mount", f"type=bind,src={patch_path},dst=/candidate.patch,readonly"] if patch_path else [] ),
                "--workdir", "/workspace", self.settings.sandbox_image,
                "sh", "-c", test_command,
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
                await asyncio.wait_for(
                    process.wait(),
                    timeout=timeout_seconds or self.settings.sandbox_timeout_seconds,
                )
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
            text = redact_sandbox_output(
                (stdout + (b"\n" if stdout and stderr else b"") + stderr).decode("utf-8", "replace"),
            )
            if timed_out:
                text += "\nSandbox time limit reached; the container was stopped."
            exit_code = process.returncode if process.returncode is not None else 1
            candidate_archive = None
            if (
                include_candidate and patch_data is not None
                and exit_code == 0 and not timed_out
            ):
                candidate_archive = await self._export_patch_candidate(
                    docker, docker_env, root, patch_path, temporary_root,
                )
            return SandboxReport(exit_code, text[-24_000:], timed_out, candidate_archive)

    async def _export_patch_candidate(
        self, docker: str, docker_env: dict[str, str], source_root: Path,
        patch_path: Path | None, temporary_root: Path,
    ) -> bytes:
        if patch_path is None:
            raise ValueError("A validated patch is required to export a candidate")
        export_root = temporary_root / "export"
        export_root.mkdir(mode=0o700)
        script_path = temporary_root / "archive_candidate.py"
        script_path.write_text(
            "import os, stat, sys, zipfile\n"
            "root = '/workspace'\n"
            "output = '/export/candidate.zip'\n"
            "max_files = " + str(self.settings.sandbox_max_files) + "\n"
            "max_bytes = " + str(self.settings.sandbox_max_expanded_bytes) + "\n"
            "files = []\n"
            "total = 0\n"
            "for current, dirs, names in os.walk(root, followlinks=False):\n"
            "    for name in list(dirs):\n"
            "        path = os.path.join(current, name)\n"
            "        if os.path.islink(path):\n"
            "            raise SystemExit('candidate contains a symlink')\n"
            "    for name in names:\n"
            "        path = os.path.join(current, name)\n"
            "        info = os.lstat(path)\n"
            "        if not stat.S_ISREG(info.st_mode):\n"
            "            raise SystemExit('candidate contains a non-regular file')\n"
            "        relative = os.path.relpath(path, root).replace(os.sep, '/')\n"
            "        files.append((path, relative, info.st_size))\n"
            "        total += info.st_size\n"
            "        if len(files) > max_files or total > max_bytes:\n"
            "            raise SystemExit('candidate exceeds configured archive limits')\n"
            "with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:\n"
            "    for path, relative, _ in sorted(files, key=lambda item: item[1]):\n"
            "        archive.write(path, relative)\n",
            encoding="utf-8",
        )
        script_path.chmod(0o600)
        job_name = f"enan-export-{uuid.uuid4().hex[:16]}"
        command = [
            docker, "run", "--rm", "--pull=never", "--name", job_name,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory", f"{self.settings.sandbox_memory_mb}m",
            "--cpus", str(self.settings.sandbox_cpu_count),
            "--user", "0:0", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--tmpfs", f"/workspace:rw,nosuid,nodev,size={math.ceil(self.settings.sandbox_max_expanded_bytes / (1024 * 1024)) + 64}m",
            "--mount", f"type=bind,src={source_root},dst=/input,readonly",
            "--mount", f"type=bind,src={patch_path},dst=/candidate.patch,readonly",
            "--mount", f"type=bind,src={script_path},dst=/archive_candidate.py,readonly",
            "--mount", f"type=bind,src={export_root},dst=/export",
            "--workdir", "/workspace", self.settings.sandbox_image,
            "sh", "-c",
            "cp -a /input/. /workspace/ && git apply --check /candidate.patch && "
            "git apply /candidate.patch && python /archive_candidate.py",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=docker_env,
            )
        except OSError as exc:
            raise RuntimeError("Rootless Docker could not export the staged candidate") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=min(120, max(10, self.settings.sandbox_timeout_seconds)),
            )
        except TimeoutError as exc:
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
            raise RuntimeError("Candidate export exceeded its time limit") from exc
        if process.returncode != 0:
            safe_output = redact_sandbox_output(
                (stdout + b"\n" + stderr).decode("utf-8", "replace"),
            )
            raise RuntimeError(
                "Isolated candidate export failed: " + safe_output[-1000:],
            )
        candidate_path = export_root / "candidate.zip"
        if not candidate_path.is_file() or candidate_path.stat().st_size > 20 * 1024 * 1024:
            raise RuntimeError("Isolated candidate export is missing or exceeds the archive limit")
        candidate = candidate_path.read_bytes()
        validate_complete_project_archive(candidate, self.settings)
        return candidate
