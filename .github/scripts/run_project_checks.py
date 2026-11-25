#!/usr/bin/env python3
"""Run repository-configured CI checks from .github/project-profile.json.

Uses only the Python standard library. Commands are argument arrays (no shell) so
that the profile is explicit and avoids shell interpolation. The repository owns
these commands; never load this profile from an untrusted external source.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / ".github" / "project-profile.json"
ALLOWED_POLICIES = {"warn", "fail"}


def report(message: str, level: str = "notice") -> None:
    # Escape workflow-command control characters so untrusted config text cannot forge commands.
    safe_message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{safe_message}")


def write_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def load_profile() -> dict[str, Any]:
    if not PROFILE.is_file():
        raise ValueError(f"Missing profile: {PROFILE.relative_to(ROOT)}")
    with PROFILE.open("r", encoding="utf-8") as handle:
        profile = json.load(handle)
    if not isinstance(profile, dict) or profile.get("schemaVersion") != 1:
        raise ValueError("project-profile.json must be an object with schemaVersion: 1")
    project = profile.get("project")
    ci = profile.get("ci")
    if not isinstance(project, dict) or not isinstance(ci, dict):
        raise ValueError("Profile must contain object properties 'project' and 'ci'")
    return profile


def detect_stacks() -> list[str]:
    """Detect common repository technologies recursively, ignoring generated/vendor trees."""
    import fnmatch

    ignored_dirs = {
        ".github", ".git", ".venv", "venv", "node_modules", "vendor", "dist", "build",
        "target", ".next", ".nuxt", ".terraform", ".cache", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", "coverage", "Pods", "DerivedData",
        "bin", "obj", "__pycache__",
    }
    exact_markers = {
        "Python": {"pyproject.toml", "requirements.txt", "requirements-dev.txt", "Pipfile", "uv.lock", "poetry.lock", "setup.py", "setup.cfg"},
        "JavaScript/TypeScript": {"package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"},
        "Deno": {"deno.json", "deno.jsonc"},
        "Go": {"go.mod", "go.work"},
        "Rust": {"Cargo.toml", "Cargo.lock"},
        "Java/Kotlin": {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew", "pom.xml"},
        ".NET": {"global.json"},
        "PHP": {"composer.json", "composer.lock"},
        "Ruby": {"Gemfile", "Gemfile.lock"},
        "Dart/Flutter": {"pubspec.yaml", "pubspec.lock"},
        "Swift": {"Package.swift", "Package.resolved"},
        "Android": {"AndroidManifest.xml"},
        "Docker": {"Dockerfile", "docker-compose.yml", "compose.yaml", "compose.yml"},
        "Terraform/OpenTofu": {".terraform.lock.hcl"},
        "Kubernetes/Helm": {"Chart.yaml", "kustomization.yaml"},
        "C/C++": {"CMakeLists.txt", "Makefile", "meson.build", "BUILD", "BUILD.bazel"},
        "Elixir": {"mix.exs", "mix.lock"},
        "R": {"DESCRIPTION", "renv.lock"},
        "Julia": {"Project.toml", "Manifest.toml"},
        "SQL / Database": {"schema.sql", "database.sql"},
        "Scala": {"build.sbt"},
        "Haskell": {"cabal.project", "stack.yaml"},
        "PowerShell": {"*.ps1", "*.psm1"},
    }
    extension_markers = {
        "Python": {".py"},
        "JavaScript/TypeScript": {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"},
        "Go": {".go"},
        "Rust": {".rs"},
        "Java/Kotlin": {".java", ".kt", ".kts"},
        ".NET": {".cs", ".fs", ".vb", ".csproj", ".fsproj", ".vbproj", ".sln", ".slnx"},
        "PHP": {".php"},
        "Ruby": {".rb"},
        "Dart/Flutter": {".dart"},
        "Swift": {".swift"},
        "C/C++": {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"},
        "Elixir": {".ex", ".exs"},
        "R": {".r", ".rmd"},
        "Julia": {".jl"},
        "Scala": {".scala", ".sc"},
        "Haskell": {".hs", ".lhs"},
        "Web assets": {".html", ".htm", ".css", ".scss", ".vue", ".svelte", ".astro"},
        "Data / notebooks": {".ipynb", ".parquet", ".arrow"},
        "SQL / Database": {".sql"},
    }
    found: set[str] = set()
    for current_root, directories, filenames in os.walk(ROOT):
        directories[:] = [name for name in directories if name not in ignored_dirs]
        # Skip irrelevant hidden directories while still scanning .github and .devcontainer.
        directories[:] = [name for name in directories if not name.startswith(".") or name in {".github", ".devcontainer"}]
        for filename in filenames:
            lower_name = filename.lower()
            for label, markers in exact_markers.items():
                if lower_name in {item.lower() for item in markers if "*" not in item}:
                    found.add(label)
                elif any("*" in item and fnmatch.fnmatch(lower_name, item.lower()) for item in markers):
                    found.add(label)
            suffix = Path(lower_name).suffix
            for label, suffixes in extension_markers.items():
                if suffix in suffixes:
                    found.add(label)
        for directory in directories:
            if directory.endswith((".xcodeproj", ".xcworkspace")):
                found.add("Swift")
    # Android and Gradle commonly share files; retain Android as an additional platform marker.
    return sorted(found)


def validate_checks(checks: Any) -> list[dict[str, Any]]:
    if not isinstance(checks, list):
        raise ValueError("ci.checks must be an array")
    if len(checks) > 50:
        raise ValueError("ci.checks may contain at most 50 checks")
    validated: list[dict[str, Any]] = []
    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            raise ValueError(f"ci.checks[{index - 1}] must be an object")
        name = check.get("name")
        command = check.get("command")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"ci.checks[{index - 1}].name must be a non-empty string")
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
            raise ValueError(f"ci.checks[{index - 1}].command must be a non-empty string array")
        working_dir = check.get("workingDirectory", ".")
        if not isinstance(working_dir, str) or not working_dir or Path(working_dir).is_absolute():
            raise ValueError(f"ci.checks[{index - 1}].workingDirectory must be a relative path")
        resolved_dir = (ROOT / working_dir).resolve()
        if resolved_dir != ROOT and ROOT not in resolved_dir.parents:
            raise ValueError(f"ci.checks[{index - 1}].workingDirectory escapes the repository")
        if not resolved_dir.is_dir():
            raise ValueError(f"ci.checks[{index - 1}] working directory does not exist: {working_dir}")
        timeout = check.get("timeoutSeconds", 900)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 7200:
            raise ValueError(f"ci.checks[{index - 1}].timeoutSeconds must be an integer from 1 to 7200")
        validated.append({"name": name.strip(), "command": command, "cwd": resolved_dir, "timeout": timeout})
    return validated


def main() -> int:
    started = time.monotonic()
    try:
        profile = load_profile()
        ci = profile["ci"]
        enabled = ci.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("ci.enabled must be true or false")
        stacks = detect_stacks()
        print("Repository profile validation: passed")
        print("Detected project technologies: " + (", ".join(stacks) if stacks else "none detected"))
        if not enabled:
            report("Project checks are disabled by project-profile.json", "warning")
            write_summary(["# Project CI", "Project checks are disabled in `.github/project-profile.json`."])
            return 0

        checks = validate_checks(ci.get("checks", []))
        policy = ci.get("noChecksPolicy", "warn")
        if not isinstance(policy, str) or policy not in ALLOWED_POLICIES:
            raise ValueError("ci.noChecksPolicy must be 'warn' or 'fail'")
        if not checks:
            message = (
                "No project-specific CI commands are configured. This run validates the kit/profile only; "
                "it does not claim that the application was built or tested. Add commands to ci.checks."
            )
            report(message, "warning")
            write_summary([
                "# Project CI",
                "<strong>Project checks are not configured.</strong>",
                "This run did not build or test application code.",
                "Add explicit command arrays to `.github/project-profile.json` after verifying the project's real toolchain.",
                "",
                "Detected technologies: " + (", ".join(stacks) if stacks else "none detected"),
            ])
            return 1 if policy == "fail" else 0

        failures: list[str] = []
        summary = ["# Project CI", "| Check | Result | Duration |", "|---|---:|---:|"]
        for number, check in enumerate(checks, start=1):
            command = check["command"]
            printable = shlex.join(command)
            print(f"\n[{number}/{len(checks)}] {check['name']}: {printable}")
            check_started = time.monotonic()
            try:
                result = subprocess.run(
                    command,
                    cwd=check["cwd"],
                    check=False,
                    timeout=check["timeout"],
                    env=os.environ.copy(),
                )
                duration = time.monotonic() - check_started
                if result.returncode == 0:
                    status = "PASS"
                else:
                    status = f"FAIL (exit {result.returncode})"
                    failures.append(check["name"])
            except FileNotFoundError as exc:
                duration = time.monotonic() - check_started
                status = f"FAIL (command not found: {exc.filename})"
                failures.append(check["name"])
            except subprocess.TimeoutExpired:
                duration = time.monotonic() - check_started
                status = f"FAIL (timeout after {check['timeout']}s)"
                failures.append(check["name"])
            print(f"Result: {status} ({duration:.1f}s)")
            escaped_name = check["name"].replace("|", "\\|")
            summary.append(f"| {escaped_name} | {status} | {duration:.1f}s |")

        total_duration = time.monotonic() - started
        summary.extend(["", f"Total duration: {total_duration:.1f}s"])
        if failures:
            summary.append("\nFailed checks: " + ", ".join(failures))
            report(f"{len(failures)} of {len(checks)} configured checks failed", "error")
            write_summary(summary)
            return 1
        print(f"\nAll {len(checks)} configured project checks passed.")
        summary.append("\nAll configured checks passed.")
        write_summary(summary)
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report(str(exc), "error")
        write_summary(["# Project CI", f"Configuration error: {exc}"])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
