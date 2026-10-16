# Secure code execution boundary

The assistant does not execute model-generated shell or Python. Mission Control has no tool registry entry for shell, arbitrary Python, file writes, deployment, or service control.

The evolution patch test runner is optional. It checks for rootless Docker before starting and runs with `--network=none`, a read-only container root, read-only source/patch mounts, dropped capabilities, `no-new-privileges`, PID, memory, CPU, and wall-time limits. The source is copied into a bounded temporary filesystem. `git apply --check` and `git apply` run only in that container before pytest. Runtime image pulls are disabled. The disposable host directory is removed on exit.

Build the trusted image from the repository root:

```sh
docker build -f deploy/code-sandbox.Dockerfile -t enan-ultra-python-sandbox:py3.15.0-pytest9.1.1 .
```

Configure a rootless Docker socket for the bot service account, set `CODE_SANDBOX_ENABLED=true`, and use a prebuilt trusted image. Add project-specific test dependencies only through a reviewed image build. The runner must not receive `.env`, bot/provider credentials, host source write mounts, or production database mounts.

Automated evolution testing rejects patches to protected security, configuration, storage, handlers, sandbox, workflow, and deployment paths. These changes need a separate reviewed release outside this automated proposal path.
