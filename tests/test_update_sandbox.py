from __future__ import annotations

import asyncio
from types import SimpleNamespace

from enan_ultra.services.code_sandbox import CodeSandbox


def test_update_candidate_uses_its_bounded_test_limits(monkeypatch) -> None:
    settings = SimpleNamespace(update_max_files=1000, update_test_timeout_seconds=180)
    sandbox = CodeSandbox(settings)
    observed: dict[str, object] = {}

    async def fake_run(
        archive_data: bytes,
        patch_data: bytes | None = None,
        *,
        max_files: int | None = None,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        observed.update({
            "archive_data": archive_data,
            "patch_data": patch_data,
            "max_files": max_files,
            "timeout_seconds": timeout_seconds,
        })
        return SimpleNamespace(exit_code=0, output="ok", timed_out=False)

    monkeypatch.setattr(sandbox, "_run_python_tests", fake_run)
    report = asyncio.run(sandbox.run_update_tests(b"merged-candidate"))

    assert report.exit_code == 0
    assert observed == {
        "archive_data": b"merged-candidate",
        "patch_data": None,
        "max_files": 1000,
        "timeout_seconds": 180,
    }
