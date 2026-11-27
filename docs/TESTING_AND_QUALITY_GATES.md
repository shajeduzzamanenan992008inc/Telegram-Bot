# Testing and quality gates

The isolated evolution gate applies the exact reviewed patch to the exact retained project ZIP and runs `python -m pytest -q --disable-warnings`. A nonzero exit, timeout, missing rootless runtime, invalid diff, protected path, or hash mismatch never produces an approval-ready proposal. Test output is bounded and secret-pattern redacted before encrypted storage.

The bot does not claim tests that were not run. `/evolve_review ID` shows actual exit status, timeout flag, result text, source hash, patch hash, and expiry. Dependency installation, lint/format/security scanning, comparison against an unmodified baseline, staging health checks, and deployment health checks are not currently part of the automatic gate. Use a trusted prebuilt image for extra deterministic checks and a human release review for deployment.

The project's local suite is run with:

```sh
.venv/bin/python -m pytest -q
```

On Windows development shells, use `.venv\Scripts\python.exe -m pytest -q`.

CI also runs Ruff's Python correctness/syntax rules (`E4`, `E7`, `E9`, `F`),
compiles all Python sources and tests, checks the Mini App JavaScript syntax with
Node.js, builds a wheel, and verifies that the Mini App and every theme JSON asset
are present in that wheel. Run the checks locally with:

```sh
python -m ruff check --select E4,E7,E9,F src tests
python -m compileall -q src tests
node --check src/enan_ultra/webapp/static/app.js
uv build --wheel --no-sources
```

The isolated evolution runner intentionally remains a test-only gate: it does not
deploy patches, restart production services, or claim staging health checks.
