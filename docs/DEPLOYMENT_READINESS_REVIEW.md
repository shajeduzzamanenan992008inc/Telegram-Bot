# ENAN_ULTRA — Final Deployment Readiness Review

## Workspace update (2026-10-17)

After the reviews below, the active Telegram `/update` handler was changed to accept an admin ZIP, safely overlay project files, preserve protected state, and replace the Python process. This later workspace change is uncommitted and was not tested or deployed. The staged-update and broker test results below describe the earlier implementation and do not verify this direct updater. This note does not change the production NO-GO or Azure evidence in the historical review.

**Review basis:** The Copilot/terminal logs supplied in this conversation. This is a consolidated review of those logs, not an independent connection to the user's Windows workspace or Azure VM.

**Decision: `BLOCKED — DO NOT ENABLE PRODUCTION DEPLOYMENT YET`**

## 1. Executive summary

The local repository has a tested ZIP-staging workflow and a local deployment-broker implementation. The supplied completion log reports **176 local tests passed**, **45 focused ZIP/deployment tests passed**, and successful Ruff, compile, frontend syntax, manifest-hash, and `git diff --check` validations. These results are local/mock evidence; they are not proof that the selected Azure production service can safely run the new code.

The selected production target is `enan-ultra.service`, which runs `python -m enan_ultra` from `/home/azureenan/Telegram-Bot-main`. Its imported package comes from that environment's `site-packages`, and its fingerprints differ from the current local source. The VM checkout has no Git metadata and lacks the local ZIP updater module. The current broker expects a different service name, account, and root-owned release directory. Deployment must remain fail-closed until source provenance and a compatible protected runtime/release layout are established.

No production deployment, service restart, production file/permission/database/credential/firewall change was reported in the supplied logs.

## 2. Evidence-based status

| Area | Status | Evidence and limitation |
|---|---|---|
| Local tests | `PASS — LOCAL ONLY` | Latest supplied completion log reports 176 full-suite passes and 45 focused ZIP/deployment passes. These do not verify live Azure integration. |
| Local quality checks | `PASS — LOCAL ONLY` | Ruff, Python compilation, frontend JavaScript syntax, source-manifest hash verification, and `git diff --check` were reported as passing. |
| Local source manifest | `PASS — FINGERPRINTS ONLY` | `docs/DEPLOYMENT_SOURCE_MANIFEST.json` records 14 local source/build/deploy files and six installed-package fingerprints; the current local entries reportedly match their sizes and SHA-256 hashes. A hash establishes byte identity, not trust or provenance. |
| Git/remote relationship | `PARTIALLY VERIFIED` | Local `main` HEAD was reported as `eed00c9cd79fd926054f9103d28e9287d6b4d45f`, matching remote `origin/main` and lightweight tag `v3.0`. The report says the commit has a signature but it could not be verified without a trusted public key. No signed build artifact was found. |
| Branch/version consistency | `UNRESOLVED` | The configured upstream `origin/upgrade/final-quality-gates` was not advertised by the remote; `pyproject.toml` says `0.1.0` while the repository tag is `v3.0`. These need an explicit release-versioning decision. |
| Local working tree | `NOT RELEASE-READY` | The supplied logs describe uncommitted and untracked deployment changes. They must be reviewed, tested, committed through the normal process, and included in the exact reviewed release artifact before deployment. |
| Actual imported Azure package | `BLOCKED` | The running interpreter imports from `/home/azureenan/Telegram-Bot-main/.venv/lib/python3.12/site-packages/enan_ultra`, not the local `src/` checkout. Relevant file hashes differ, and the installed package lacks `services/telegram_zip_updates.py`. The VM checkout has no Git metadata. |
| Broker/runtime compatibility | `BLOCKED` | The local broker expects `telegram-ai.service`, service user `enan-ultra`, `/opt/enan-ultra`, and a root-owned application tree. The chosen target is `enan-ultra.service`, user `azureenan`, `/home/azureenan/Telegram-Bot-main`; the runtime tree is reported as owner-writable (`0775`). The broker/helper/socket/enable flag and backup/history locations were absent on the VM at inspection. Do not weaken the broker's path/ownership checks to bypass this mismatch. |
| Runtime user privilege | `SECURITY REVIEW REQUIRED` | Supplied logs say the runtime account is in the `sudo` group; the effective sudo policy query did not complete successfully. Confirm actual grants using a safe, read-only review before any migration. |
| Package-service health | `PARTIALLY VERIFIED` | The service was active and had `NRestarts=0` at one inspection. This does not prove Telegram, database, provider, or application-level health. No live integration test was performed. |
| Separate Telethon service | `VERIFIED — SEPARATE ISSUE` | `telegram-ai.service` runs `/home/azureenan/personal-telegram-ai/assistant.py`; journal evidence showed `EOFError` in Telethon's interactive authentication path, 19,633 process exits, and 19,632 scheduled restarts. Its systemd `NRestarts` was 13,968; the discrepancy from retained journal counts was not explained. Do not conflate this service with the selected package target. |
| Telethon session permissions | `SECURITY RISK` | The session file was reported as mode `0644`. Its contents were not read. Treat it as sensitive authorization state and correct access only after confirming ownership and recovery requirements. |
| Credential exposure | `ACTION REQUIRED` | A prior command accidentally emitted credential values. The OpenRouter key must be treated as compromised and revoked/replaced. Do not put the exposed value in reports or source. The Telegram MTProto app credentials/session need a separate authorized remediation review; do not assume a specific revocation/reissue behavior without verification. |
| Production deployment/rollback | `NOT VERIFIED / BLOCKED` | No live deployment or service restart occurred. Local design/tests do not demonstrate that the broker is installed, enabled, correctly privileged, or compatible with the selected VM layout. |

## 3. Canonical-source and release-provenance conclusion

The available evidence is insufficient to authorize release:

1. A local commit reportedly matches remote `main` and the lightweight `v3.0` tag, but signature trust was not established.
2. The release version in project metadata differs from the tag version.
3. The checked-out branch's configured upstream was not present in the remote's advertised refs.
4. Local deployment changes are uncommitted/untracked, so the current working tree is not represented by the matching commit alone.
5. The production VM runs an installed package with different content, no repository metadata, and no ZIP-update module.
6. There is no independently verifiable signed build artifact connecting the local source, the tested package, and the installed production package.

**Therefore, do not infer that a remote-main match alone proves that the local working tree or installed VM package is trustworthy.**

## 4. Required remediation plan

### P0 — Security and identity

1. Revoke/delete the exposed OpenRouter API key, generate a new one, and update every confirmed consumer using protected configuration. Never retrieve or print the old value again.
2. Review the MTProto API credentials and Telethon session through the provider's authorized procedures. Restrict session-file access after confirming owner, active session use, backup/recovery needs, and login recovery.
3. Confirm the actual effective sudo grants for `azureenan`. Do not install a privileged deployment broker while the runtime account's privilege boundary is unclear.
4. Keep `UPDATE_DEPLOYMENT_ENABLED=false`; do not install or enable the broker/socket on the VM during this phase.

### P1 — Establish an authoritative source

1. Decide the project's authoritative repository, branch, commit, tag, and versioning policy.
2. Verify the commit/tag signature against a trusted public key obtained independently; if this cannot be done, record that provenance remains unverified rather than treating an unsigned or unverifiable artifact as trusted.
3. Resolve the `pyproject.toml` `0.1.0` versus `v3.0` discrepancy and the missing upstream branch intentionally; do not silently rewrite Git history or drop existing work.
4. Review all uncommitted/untracked files, tests, manifests, and deployment changes. Commit through the normal review process after tests pass.
5. Build a reproducible release artifact from that exact reviewed commit in a clean build environment. Produce a manifest and, ideally, a verifiable signature/attestation for the artifact.
6. Compare the candidate release manifest to the actual imported VM package. Do not claim a match until all required runtime files and dependency metadata are accounted for.

### P1 — Design a compatible protected runtime

1. Prepare a migration design for a dedicated, root-owned application/release directory (for example `/opt/enan-ultra`) that is not writable by the bot runtime user.
2. Keep the bot running as an unprivileged service account. The deployment helper should be a separate, narrowly scoped privileged component with fixed service/path allowlists and strict request validation; no unrestricted sudo or arbitrary command interface.
3. Move secret configuration to a protected environment file with least-privilege access after inventorying how the current app loads environment variables. Do not print secret values while migrating.
4. Determine the actual resolved database path and all persistent state/upload/sticker paths first. Plan separate writable state locations and backups; do not copy or replace the database merely as part of code deployment.
5. Review systemd `User`, `Group`, `WorkingDirectory`, `ExecStart`, environment file, restart policy, state directory and sandbox directives as one consistent unit.
6. Keep the deployment broker disabled until the exact service, account, path and persistent-data mapping pass the broker's strict preflight checks. Do not relax safety checks simply to make the current home-directory checkout pass.

### P2 — Validate deployment and recovery in disposable staging

1. Test the selected service layout using a disposable Linux harness that reproduces the planned ownership, interpreter/package path, systemd unit shape and data mounts.
2. Verify archive path checks, protected paths, manifest integrity, exact-hash approval, expired/replayed approval rejection, privilege boundaries and audit logging.
3. Test backup creation and capacity checks, interrupted deployment recovery, service restart failure, health-check failure and rollback in the disposable environment.
4. Ensure dependency changes and database migrations require separate reviewed procedures; do not run arbitrary dependency installers or migrations from an uploaded ZIP.
5. Rehearse a release from the exact signed/verified artifact and document expected service and data state before/after.

### P2 — Handle the separate Telethon issue

1. Keep `telegram-ai.service` out of the `enan-ultra.service` deployment plan.
2. Use an authorized one-time interactive operator flow to establish a valid session if needed; do not try to answer login prompts from a non-interactive systemd service.
3. Confirm session ownership and restrict permissions; startup should verify the session exists and is authorized, then connect non-interactively and fail clearly if authentication is missing.
4. After separately approved remediation, observe actual logs and restart behavior. Do not assume the historical restart-loop counts equal the systemd counter.

### P3 — Controlled production rollout (only after blockers clear)

1. Confirm source provenance, artifact signature, canonical service identity, root-owned release layout, backup paths, secret access and tested rollback.
2. Keep staging approval and production approval distinct. Bind production approval to the exact artifact/manifest digest.
3. Back up persistent data according to a tested procedure and verify backup integrity before activation.
4. Deploy via the reviewed helper; do not allow the bot's general Python process to write its own code or invoke arbitrary privileged commands.
5. Run service and application health checks without sending unwanted real-user messages or incurring uncontrolled API costs.
6. Roll back only through a rehearsed safe path, taking database migration compatibility into account.
7. Notify the admin with the actual deployment/health/rollback results; never report success before confirmation.

## 5. Release gate checklist

Production deployment remains blocked until all applicable items are positively verified:

- [ ] Exposed credentials have been remediated; no secrets appear in reports/logs.
- [ ] Trusted canonical source commit and release version are established.
- [ ] Signature or artifact provenance has been verified, or residual trust limitations have been formally accepted by the operator.
- [ ] All local working-tree changes are reviewed and included in the exact release artifact.
- [ ] The artifact manifest matches the package intended for deployment.
- [ ] The runtime user cannot modify production application code or deployment policy.
- [ ] A narrowly scoped deployment broker is installed/configured for the exact selected service and remains fail-closed.
- [ ] Environment, database and persistent state paths are mapped and protected.
- [ ] Backups and rollback have passed disposable integration tests.
- [ ] Staging syntax/tests pass on the exact release artifact, with no mandatory `BLOCKED` checks.
- [ ] Staging approval and distinct digest-bound production approval work end-to-end.
- [ ] Post-deploy health checks and failure recovery have been rehearsed.
- [ ] Operator explicitly authorizes the production rollout.

## 6. Scope and limitations

This review consolidates the supplied Copilot logs. It does **not** independently execute Git, access the Windows repository, validate the remote signing key, or connect to Azure from this conversation. The reported local test numbers and VM findings are attributed to the supplied logs. No production operation is authorized by this document.

## 7. Local workspace follow-up — 2026-10-17

This addendum independently checks the Windows workspace. It updates the local Git, test, and artifact evidence above; the production VM details remain from the earlier supplied read-only logs and were not rechecked in this phase.

### Current local evidence

- `HEAD` is `eed00c9cd79fd926054f9103d28e9287d6b4d45f`. A fresh `git ls-remote` check found `origin/main` and lightweight tag `v3.0` at that commit. The commit signature is present but `git verify-commit` returns `E` because the signer public key is unavailable locally. The local `main` branch still tracks `origin/upgrade/final-quality-gates`, which was not advertised by the remote; `pyproject.toml` reports version `0.1.0` while the tag is `v3.0`.
- At first inventory there were 11 modified tracked files and 13 untracked files. Source, service, config, and test changes were preserved. This follow-up appended to this report and created one artifact manifest; the source ZIP and wheel are ignored by the repository's existing ignore rules.
- All 14 local file fingerprints in the existing `DEPLOYMENT_SOURCE_MANIFEST.json` match the current working files. Its VM package fingerprints are historical evidence and were not independently refreshed.
- The full current working tree passed **176 tests in 53.61 seconds** under the local Python 3.15 virtual environment. This includes uncommitted and untracked code.
- A clean archive of the exact `HEAD` commit imported from the extracted archive and passed **131 tests in 55.26 seconds**. Hatchling 1.32.4 built a wheel; the wheel passed ZIP integrity and import-origin checks. `git diff --check` passed. These are local Windows checks, not Linux/systemd staging results.
- A value-redacting scan found no OpenRouter-key-shaped value in the changed source or `.env.example`; secret-like strings in the new tests are redaction fixtures. The values were withheld. This does not inspect ignored secret files or erase the prior exposure from logs.

### Review artifact from the exact commit

The candidate contains committed `HEAD` only; it deliberately excludes all local modifications and untracked files. It is not the uncommitted updater feature and is not approved for production.

| File | SHA-256 |
|---|---|
| `release-artifacts/readiness-eed00c9cd79f-20261017/enan-ultra-source-eed00c9cd79f.zip` | `2a251bb21c3187cb781054ab6e695a31c569869966f65ccce0e8b9ab33817e1f` |
| `release-artifacts/readiness-eed00c9cd79f-20261017/wheel/enan_ultra_telegram_ai-0.1.0-py3-none-any.whl` | `9eab4e2dcd20d53765ad6f5355c7c2a71adc4373b65cdfbac886e70a28ee4093` |

The build record is [`release-artifacts/readiness-eed00c9cd79f-20261017/manifest.json`](../release-artifacts/readiness-eed00c9cd79f-20261017/manifest.json). This is an unsigned review artifact. Commit-signature trust, release-version policy, Python 3.12 Linux staging, and a target-compatible migration remain unresolved.

The pre-existing `DEPLOYMENT_SOURCE_MANIFEST.json` was not overwritten; its statement that no wheel was available describes the earlier audit snapshot and predates this follow-up artifact.

### Mismatch and blockers confirmed by local review

- The supplied VM evidence says `enan-ultra.service` imports `enan_ultra` from the service virtualenv's `site-packages`, while the local updater initializes its source root from `Path.cwd()`. Those paths do not establish that the files reviewed by the updater are the package the service executes. The VM evidence also says the installed package lacks the ZIP updater module; this remains a historical observation until read-only access is repeated.
- The local broker and unit are hard-coded for `telegram-ai.service`, `/opt/enan-ultra`, and account `enan-ultra`. The reported target is `enan-ultra.service` under `/home/azureenan/Telegram-Bot-main`; the supplied logs identify `telegram-ai.service` as a separate Telethon process. The broker's service identity and imported-package paths therefore do not match the selected target. Do not install or enable this broker on that VM.
- The draft unit uses `WorkingDirectory=/opt/enan-ultra` and protects the filesystem except `/var/lib/telegram-ai`, while `DATABASE_PATH` still defaults to the relative path `data/telegram_ai.sqlite3`. That would resolve under the protected application tree. The installer excludes `data/` from its source copy and does not map the existing database into the state directory. A migration must set an explicit durable database path and preserve the database, SQLite WAL/SHM files, the other inventoried data files, uploads, stickers, and runtime state.
- The installer keeps `/etc/telegram-ai/bot.env` if it already exists, but otherwise seeds it from `.env.example`; it does not migrate the current home-directory `.env`. It also enables the `telegram-ai-deploy.socket` and `telegram-ai.service` units. Do not run it against production. The socket must remain disabled by default, and no production service should be enabled until the chosen unit, config, data mapping, and staging gates agree.
- The local draft broker applies permitted source files in place; it is not a versioned, immutable release manager and validates hashes/approvals rather than a trusted source signature. It cannot resolve the installed-wheel/source-checkout mismatch. The current production deployment toggle is not freshly confirmed; no production operation occurred here.
- The earlier deployment draft proposes the existing `azureenan` account for persistent-state ownership. Use a dedicated `enan-ultra` system account with no login shell, no sudo grant, and only the minimum required state access. Keep application releases, systemd units, broker code, policy, and backups root-owned and not writable by that runtime account.

### Proposed protected layout for staging review

Use versioned releases and external mutable state:

```text
/opt/enan-ultra/releases/<reviewed-commit>/   root:root; runtime read-only
/opt/enan-ultra/current -> releases/<reviewed-commit>  root-controlled atomic switch
/etc/enan-ultra/bot.env                     root:root, mode 0600
/var/lib/enan-ultra/                        enan-ultra:enan-ultra, mode 0700
/var/lib/enan-ultra-deploy/                 root:root, mode 0700
```

The unit should be consistently named `enan-ultra.service`, run as the dedicated unprivileged account, load configuration from the protected file, and have write access only to explicitly mapped persistent state. Set `DATABASE_PATH` to the preserved database under `/var/lib/enan-ultra/`; map sticker storage, health markers, uploads, and any other existing data before changing `WorkingDirectory`. Keep the broker socket and deployment flag absent/disabled until a separate, reviewed migration. Replace in-place file activation with a digest-bound, signed release and atomic `current` switch, with a verified previous release and backup retained for rollback.

### Operator checklist — OpenRouter credential

No credential was changed in this phase. Treat the previously exposed key as compromised. In OpenRouter's authenticated key-management controls, revoke/delete the exposed key promptly; create a replacement, store it directly in the approved secret manager, and update only confirmed consumers (production, staging, development, or CI as applicable). Do not paste either value into chat, shell history, tickets, reports, logs, or Git. OpenRouter documents that a newly created key's plaintext is shown only once and supports optional spending limits and expiry; its delete operation is documented in the official [create-key](https://openrouter.ai/docs/api/api-reference/api-keys/create-keys) and [delete-key](https://openrouter.ai/docs/api/api-reference/api-keys/delete-keys) references. Review provider usage for unexpected activity. Search configuration and logs using redacted pattern checks that report filenames only; any cleanup or Git-history remediation is a later, separately reviewed action.

### Required gates before any production migration

1. Verify the trusted signer key through an independent channel; choose and reconcile the release version; review and commit the dirty changes through the normal process. Rebuild the artifact from that exact reviewed commit and record its digest.
2. Repeat read-only inspection of `enan-ultra.service`: account, group, working directory, environment-file path, process import origin, package hashes, code/config/data ownership and modes. Never print environment contents. Confirm whether `azureenan` has sudo rights.
3. Inventory environment variable names without values and enumerate data filenames, sizes, owners, permissions, SQLite WAL/SHM files, uploads, stickers, and external state. Define a copy plan that preserves the existing environment and database; verify a consistent backup and restore in disposable staging.
4. Correct the local unit, installer, updater source root, broker service allowlist, paths, account and state mapping. Keep the production socket and enable flag disabled by default.
5. Run the exact artifact in disposable Linux/systemd staging with Python 3.12. Verify read-only code access for the runtime user, import origin, config loading without outputting values, database health, backup integrity, activation interruption recovery, rollback, and service identity. Do not send user messages or incur provider API costs during health checks.
6. Review the concrete migration/rollback steps, downtime, backup location, data ownership, artifact digests, and recovery owner. Require a separate explicit operator authorization before any production migration. Until then, readiness remains **BLOCKED**.

## 8. Read-only production inventory and staging preparation — 2026-10-17

### Local evidence rechecked in this phase

- `git rev-parse HEAD` returned `eed00c9cd79fd926054f9103d28e9287d6b4d45f`. `git ls-remote origin HEAD refs/heads/main refs/tags/v3.0` showed all three refs at that commit.
- `git verify-commit HEAD` exited 1. The commit has a signature, but local verification remains unavailable because the signer public key is not present; provenance is not cryptographically trusted yet.
- The review manifest records the source ZIP SHA-256 as `2a251bb21c3187cb781054ab6e695a31c569869966f65ccce0e8b9ab33817e1f` and the wheel SHA-256 as `9eab4e2dcd20d53765ad6f5355c7c2a71adc4373b65cdfbac886e70a28ee4093`. Recomputed hashes matched. Both ZIP containers passed integrity checks.
- A read-only ZIP/Git blob comparison checked all 133 source ZIP files against `git ls-tree` and `git cat-file blob` at the manifest commit: no archive path was outside that commit and no file content differed. The wheel contains 52 `enan_ultra/` package files; all 52 paths and bytes match the package tree at that commit. Wheel metadata reports version `0.1.0`. The wheel does not embed a commit ID, so its commit association comes from the manifest plus this exact content comparison.
- The 14 source-manifest local file fingerprints still match their working-tree files (14/14; zero missing or mismatched). `git diff --check` exited 0. The dirty worktree still has 11 modified tracked files and 14 untracked files; these were preserved. No source file, credential, service, permission, database, firewall, or production file was changed in this phase. This section is an additive report update.
- The prior local results remain: 176 tests passed in 53.61 seconds on the dirty Windows worktree under Python 3.15; 131 tests passed in 55.26 seconds from a clean archive of the exact commit, also on Windows/Python 3.15. These are not Linux 3.12 or systemd staging results. No new test suite was run in this phase.

### Azure inventory availability and boundary

`Get-Command az` returned not installed. No Azure-specific MCP tool is available in this session. No subscription, resource group, VM resource ID/name, or authorized SSH route was established. Therefore no Azure query or production-host command was run; the current VM, both services, broker/socket, feature flag, environment files, databases, permissions, and runtime account remain unverified in this phase.

The source manifest contains historical, not live-rechecked, evidence: `enan-ultra.service` was reported with working directory `/home/azureenan/Telegram-Bot-main`, entry point `python -m enan_ultra`, and package import under `.venv/lib/python3.12/site-packages/enan_ultra`; that checkout had no Git metadata or independently authenticated release artifact. Treat those paths and the older database/service findings elsewhere in this report as leads only, not current facts. Do not infer the Azure target from the account name or those paths.

After an operator identifies the intended subscription and VM, the following Azure CLI commands are read-only inventory commands. Do not run `az account set`, deployment, extension, restart, stop, or configuration commands. Microsoft documents the subscription listing and VM show/instance-view commands in the [Azure CLI account](https://learn.microsoft.com/en-us/cli/azure/account?view=azure-cli-latest) and [Azure CLI VM](https://learn.microsoft.com/en-us/cli/azure/vm?view=azure-cli-lts) references.

```bash
az account list --all --query "[].{name:name,id:id,isDefault:isDefault}" --output table
# Operator selects the exact subscription ID and VM resource ID from authorized inventory.
az vm list --subscription '<OPERATOR_CONFIRMED_SUBSCRIPTION_ID>' --query "[].{name:name,resourceGroup:resourceGroup,id:id}" --output table
az vm show --ids '<OPERATOR_CONFIRMED_VM_RESOURCE_ID>' --query "{id:id,location:location,resourceGroup:resourceGroup,vmSize:hardwareProfile.vmSize,identityType:identity.type}" --output json
az vm get-instance-view --ids '<OPERATOR_CONFIRMED_VM_RESOURCE_ID>' --query "instanceView.statuses[].code" --output tsv
```

Using an already authorized read-only SSH route to that confirmed host, inventory service properties without printing environment values or command-line arguments:

```bash
sudo systemctl show enan-ultra.service --no-pager \
  -p LoadState -p ActiveState -p SubState -p User -p Group -p SupplementaryGroups \
  -p WorkingDirectory -p FragmentPath -p MainPID -p EnvironmentFiles \
  -p NoNewPrivileges -p CapabilityBoundingSet -p AmbientCapabilities \
  -p ProtectSystem -p ReadWritePaths -p ReadOnlyPaths -p PrivateTmp
sudo systemctl show telegram-ai.service --no-pager \
  -p LoadState -p ActiveState -p SubState -p User -p Group -p SupplementaryGroups \
  -p WorkingDirectory -p FragmentPath -p MainPID -p EnvironmentFiles \
  -p NoNewPrivileges -p CapabilityBoundingSet -p AmbientCapabilities \
  -p ProtectSystem -p ReadWritePaths -p ReadOnlyPaths -p PrivateTmp
sudo systemctl list-unit-files --type=service --no-pager --no-legend | awk 'tolower($1) ~ /enan|telegram|deploy/ {print $1, $2}'
sudo systemctl list-unit-files --type=socket --no-pager --no-legend | awk 'tolower($1) ~ /enan|telegram|deploy/ {print $1, $2}'
sudo systemctl list-sockets --all --no-pager | awk 'tolower($0) ~ /enan|telegram|deploy/'
```

For each discovered broker/helper unit, record `LoadState`, `ActiveState`, `User`, `Group`, `FragmentPath`, `EnvironmentFiles`, and `Listen`; use `systemctl is-enabled` and `systemctl is-active` only to read its current state. Do not use `systemctl cat`, `systemctl show -p Environment`, `printenv`, `ps ... args`, or journal output because they can reveal secret values. If checking the proposed `UPDATE_DEPLOYMENT_ENABLED` flag, first confirm that exact key belongs to the installed version, then use a redacting parser that emits only `unset`, `disabled`, `enabled`, or `other`.

For the live process, obtain only the executable and working directory with `readlink -f /proc/<MainPID>/exe` and `/proc/<MainPID>/cwd`; list environment variable names only with `tr '\0' '\n' < /proc/<MainPID>/environ | sed 's/=.*//' | sort -u`. Reproduce the package import-origin check under the discovered service account, interpreter, and working directory with bytecode writes disabled; print only `enan_ultra.__file__` and package version. If `PYTHONPATH` or another import-affecting setting is present, account for it without displaying unrelated values.

Use only paths discovered from those service properties and the application configuration. For each environment file and state/database path, use `stat -c '%A %a %U:%G %n' <path>` and `namei -om <path>`; list environment variable names with `awk` but never right-hand-side values. Enumerate database, WAL/SHM, uploads, sticker, and other persistent filenames with `find` metadata only. Check runtime identity with `id <discovered-user>`, `sudo -n -l -U <discovered-user>`, and writable paths with `sudo -u <discovered-user> test -w <discovered-path>`. Inspect `telegram-ai.service` independently; do not infer its ownership or state from `enan-ultra.service`. No state, file, account, or permission changes are part of this inventory.

### Separate staging plan; no results claimed

1. Use an isolated disposable Linux VM matching the intended Ubuntu/systemd target and Python 3.12. Start from the verified exact-commit source archive and separately install the exact wheel. Do not copy production `.env`, databases, user data, or credentials into staging; use mocks or staging-only credentials held outside command output and logs.
2. In a clean extraction, install the locked dependencies with `uv sync --locked --python 3.12 --group test`; install the verified wheel into a clean Python 3.12 environment and confirm `import enan_ultra` resolves inside that environment to the wheel. Record Python, OS, dependency lock and artifact digests. Verify dependency consistency and run the clean-commit test suite there.
3. Determine migrations from the exact running and candidate versions before applying any. Use a disposable copy of the enumerated database/state only. Exercise the migration, SQLite `integrity_check`, application reads/writes, and repeat startup; production data is not a test fixture.
4. Define health signals from the app and unit rather than assuming an HTTP endpoint. Verify process identity, import origin, config loading without value output, database readiness, and a non-delivery Telegram/provider smoke check or mocks. Do not send user messages or incur production provider usage.
5. Create a consistent staging backup of all mapped persistent state (including SQLite WAL/SHM handling), restore it into a fresh disposable state directory, and verify integrity and application startup from the restored copy.
6. Exercise activation failure, interruption before/after the version switch, service health failure, and rollback to the prior immutable release. Prove the previous release and backup remain usable and assess database backward compatibility. Capture logs only after secret redaction.

No Linux/Python 3.12 staging, migration, backup/restore, health, or rollback test has been performed. Those gates remain outstanding. Production deployment remains **NO-GO**; the broker and feature flag were not enabled or changed, and their current production state is unknown.

### Operator actions still required

- Identify the intended Azure subscription, VM resource ID, and an approved read-only access route; capture the inventory above without sharing secret values.
- Treat the previously exposed OpenRouter credential as compromised and complete the existing operator-only revocation/replacement checklist in section 7. No credential was changed here.
- Establish signer trust independently, resolve the `0.1.0` versus `v3.0` release version mismatch, and reconcile the actual installed package/service with the reviewed source.
- Map every configuration file, database, WAL/SHM file, and persistent state path; then prove backup restoration and rollback in disposable Linux/Python 3.12 staging before presenting any production migration for separate authorization.

## 9. Telegram ZIP update audit and regression results — 2026-10-17

### Local audit findings

- PASS (automated unit coverage): ZIP authorization checks, isolated upload staging, hash-bound staged release, expiry/replay handling, and approval callbacks are covered by the feature tests. The test named test_stages_in_isolation_and_hash_binds_exact_release confirms an uploaded archive is staged separately and the live source remains unchanged before approval. These are local test results, not a Telegram production exercise.
- PASS (automated unit coverage): archive path traversal, absolute/backslash paths, duplicate and case-colliding names, symlink/special entries, encrypted or malformed entries, size/ratio limits, protected paths, and staged-hash tampering are rejected by tests. Added regression coverage for an explicit directory whose case-folded name collides with a file's implicit parent.
- PASS (automated unit coverage): broker preflight, staged-release validation, source snapshot, health-gated activation, and simulated rollback paths are covered with temporary directories, fake systemd, and test sandbox implementations. These tests do not exercise a real Linux host or real rootless container sandbox.
- NOT VERIFIED: end-to-end Telegram upload/approval against a bot, actual broker/socket activation, real systemd preflight, real container isolation, Azure state, production feature-flag state, and production service state.
- Static configuration blocker (not live-host evidence): the proposed unit uses /opt/enan-ultra as its working directory and allows writes under /var/lib/telegram-ai. The example DATABASE_PATH is relative (data/telegram_ai.sqlite3), which resolves under the application working directory, while the runtime health marker is written beside the database and the broker expects /var/lib/telegram-ai/runtime-health.json. This proposed layout must be reconciled and tested before staging; the actual production data path is unknown and must not be inferred from it.
- State-preservation blocker: the broker's tested release snapshot covers source files; database, environment, and persistent user data need an independently designed consistent backup and restore procedure. No backup or restore was performed.
- The local .env.example and config default keep UPDATE_DEPLOYMENT_ENABLED=false. The local broker draft source is untracked and has the hardening described below, but no production broker, service, flag, credential, permission, database, or Azure resource was changed. Production state remains unknown.
- The current dirty ZIP feature and broker changes have no reviewed commit or release artifact binding them. The prior clean artifact does not prove or include this dirty worktree feature; review, commit, rebuild, and provenance verification remain required.

### Confirmed local fixes

1. ZIP explicit-directory case collision: a reproducer archive containing src/enan_ultra/Code/ and a file below src/enan_ultra/code/ was accepted before the fix. Archive validation now records explicit directory paths as well as inferred file parents and rejects case-fold collisions. Regression tests verify rejection, no staging directory creation, and unchanged live source.
2. Broker service executable validation: before the fix, preflight accepted an ExecStart whose actual executable was /tmp/untrusted when the expected venv interpreter appeared only as an argument. Preflight now parses the systemd path= executable and argv[] and requires the expected interpreter followed by exactly -m enan_ultra. A regression test covers the malformed entrypoint. No systemd service was queried or changed.

### Test results

Commands executed in this Windows worktree with Python 3.15:

- C:\Python315\python.exe -m pytest tests/test_telegram_zip_updates.py tests/test_telegram_deployment.py -q — final: 48 passed in 13.16s (exit 0).
- C:\Python315\python.exe -m pytest -q — final: 179 passed in 39.39s (exit 0).
- git diff --check — PASS (exit 0; no whitespace errors in tracked-file diff).

The first pre-fix targeted run was 45 passed in 27.50s. An intermediate broker parser attempt produced 10 failed, 38 passed because its expression did not match the existing fake-systemd fixture format; the parser was corrected, then both final commands above passed. These results are Windows/Python 3.15 only. Linux/Python 3.12 staging, dependency installation there, migrations, health checks on systemd, backup restoration, rollback on a host, Azure inventory, and live feature-flag/broker state are NOT VERIFIED.

### Required staging and operator steps

1. Review and preserve the full dirty worktree. Once the ZIP/broker changes and all related configuration are approved, create a reviewed source commit; verify the commit contents and provenance, then build a new artifact from that exact commit and verify its manifest hashes and signer. Do not use the older clean artifact as evidence for these uncommitted changes.
2. Use a disposable Linux VM matching the intended OS/systemd and Python 3.12. From a clean checkout of the reviewed commit, verify the lock file and run uv sync --locked --python 3.12 --group test, then uv run --python 3.12 pytest -q. Install the built wheel in a separate clean Python 3.12 environment and record the package import path and artifact digest.
3. Before installing the draft units in staging, decide and document the application root, service account, state root, environment-file path, database path, health-marker path, and socket access. Make the database and health-marker paths consistent with unit write restrictions and broker expectations. Keep the deployment flag false for initial startup; use staging-only credentials stored outside command lines and logs.
4. In a disposable staging state directory, exercise migrations against a copy of representative data, application startup/readiness, database integrity and normal reads/writes, and health failure detection. Do not use production credentials or production data as a test fixture.
5. Make a consistent staging backup of all state, including SQLite WAL/SHM handling; restore it into a fresh disposable directory and verify database integrity and application startup from the restored copy.
6. Exercise broker preflight and approval with a real isolated sandbox, then inject activation failure, interrupted activation, failed health check, and rollback. Verify the previous immutable release remains runnable and assess database backward compatibility. Capture only redacted logs and evidence.
7. Only after source provenance, permissions, state mapping, security review, backup/restore, staging, and rollback gates pass should an operator present a separate production migration proposal for authorization. No production action is authorized by this report.

Decision: production NO-GO. The automated local Windows test suite passes, but release provenance for the dirty feature, Linux/Python 3.12 staging, runtime/state-path reconciliation, real sandbox/systemd behavior, backup restoration, rollback, and actual Azure/service/broker/flag inventory remain blockers.

## 10. Reviewed release candidate and staging configuration — 2026-10-17

### Source selection and provenance

- Reviewed and committed exactly 23 release-related files in commit 4448a84786fe88afaf9ea067a0aca4742ba46184 (tree 0e4a8048ab021207fc3786fee27daaadf35ebafc; parent eed00c9cd79fd926054f9103d28e9287d6b4d45f). The selected set covers ZIP configuration/handlers/runtime/storage, the ZIP manager and broker, systemd/installer configuration, feature documentation, and their tests.
- The readiness report, deployment source manifest, historical feature-verification report, and prior artifact manifest were not staged or committed; they remain in the worktree. No other pre-existing work was reset or discarded. The current tracked worktree matches the new commit.
- The new commit is unsigned. No signature is claimed or verified. The prior v3.0 Git tag is a lightweight tag on the parent source revision, while pyproject.toml, src/enan_ultra/__init__.py, and the existing wheel consistently identify package version 0.1.0. The deliberate candidate decision is to use 0.1.0 as the package version and the full commit hash as source identity; v3.0 is not used as package metadata and was not retargeted.

### Candidate artifact verification

Candidate directory: release-artifacts/staging-candidate-4448a84786fe/

- Source archive: source.zip; SHA-256 4a613bb2cb0ccbe8caa308f5f204e9791d161e2475a74575069b1739e56f7285. All 145 archive files were compared byte-for-byte with blobs from the committed tree.
- Wheel: wheel/enan_ultra_telegram_ai-0.1.0-py3-none-any.whl; SHA-256 51452930a6d5fc1e13249f0e590647a3e0429fc83d27306bfbb68f70fe5057bd. Wheel metadata, pyproject.toml, and package __version__ all report 0.1.0. All 54 wheel package files were compared byte-for-byte with committed source package blobs.
- A separate temporary target install with pip --no-deps --no-index passed; import origin resolved inside that installed wheel target and reported version 0.1.0. Build and import checks used Windows Python 3.15, not Linux/Python 3.12.
- Candidate manifest: release-artifacts/staging-candidate-4448a84786fe/manifest.json; SHA-256 7302f22c70407408e1d7f800aefce346dd31f46cc8fb89423d4dcb3ef0a7207e. The manifest binds commit/tree, package version, lock hash, artifact hashes, file counts, and unsigned-signature status. Hashes were re-read and matched after manifest creation.

### State-path and activation safety changes

- Exact base-unit mismatch: WorkingDirectory is /opt/enan-ultra; ReadWritePaths permits /var/lib/telegram-ai; default DATABASE_PATH is relative data/telegram_ai.sqlite3, resolving under /opt/enan-ultra/data. The runtime writes runtime-health.json beside the database, but the broker expects /var/lib/telegram-ai/runtime-health.json. Thus the default proposed layout places both database and marker outside the declared writable path and the marker at the wrong broker path.
- Added a staging-only EnvironmentFile drop-in and a non-secret staging state override. The drop-in clears the base environment-file list, loads the staging-only full configuration, then loads the state override last. That override sets DATABASE_PATH to /var/lib/telegram-ai/telegram_ai.sqlite3; its parent is the unit's writable root, and the runtime marker resolves to the broker's expected path. A regression test checks both the default mismatch and staging-path alignment. This is not a production data-path decision. systemd documents that an empty EnvironmentFile assignment resets the prior list and that later environment files override earlier files: https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml.
- The Ubuntu installer no longer enables the deployment socket by default. A regression test guards against automatic socket enable/start. The local feature flag remains false by default. No installer, unit, socket, flag, or production host was activated or modified in this phase.

### Test results

Commands executed on Windows with C:\Python315\python.exe:

- python -m pytest tests/test_telegram_zip_updates.py -q: 28 passed in 9.61s.
- python -m pytest tests/test_telegram_deployment.py -q: 22 passed in 15.10s.
- python -m pytest -q: 181 passed in 40.52s.
- git diff HEAD^ HEAD --check: PASS, exit 0.

Coverage mapping: tests/test_telegram_zip_updates.py covers manager admin/chat authorization, expiry, archive rejection, safe staging, hash integrity, validation gates, staging callbacks, separate production approval and replay rejection. tests/test_telegram_deployment.py covers broker snapshots, source/data preservation, malformed requests, preflight layout checks, activation interruptions, health failures, rollback simulations, staging state-path alignment, and the installer's default-disabled broker socket. These use local fixtures/fakes; they do not verify live Telegram, systemd, Azure, or rootless Docker behavior.

### Staging availability and remaining gates

- No approved Linux staging host or access route was available. The Windows WSL command returned usage instead of a distribution list; uv, Docker, and systemd-analyze commands were unavailable. A Windows Python 3.12 interpreter exists, but pytest dependencies were incomplete, including python-telegram-bot, aiosqlite, cryptography, and hatchling. No Linux/Python 3.12 command, service configuration check, migration, health check, backup/restore, broker activation, deployment interruption, or rollback test ran.
- Required staging prerequisites: an approved disposable Linux VM matching the intended systemd target; Python 3.12; uv; the pinned offline/rootless sandbox image and Docker runtime; a staging-only environment file with no production values; the staging-only state drop-in; and disposable SQLite data for migration/backup/restore tests.
- On that host, from a clean checkout of this commit: run uv sync --locked --python 3.12 --group test; then run the ZIP suite, deployment suite, and full suite with uv run --locked --python 3.12 pytest. Export the locked dependency set with uv export --locked --no-dev --group test --no-emit-project --format requirements-txt, sync it into a separate wheel-check virtual environment with uv pip sync, install the candidate wheel without dependencies, and confirm the import path and version come from that wheel.
- Install and verify the staging-only drop-in on that disposable host; confirm effective WorkingDirectory, ReadWritePaths, EnvironmentFiles, and database/health-marker paths without printing environment values. Then test migrations, SQLite integrity, consistent backup and restore, application readiness, real sandbox behavior, interrupted activation, health failure, and rollback using disposable state only.
- Actual Azure subscription/VM identity, production service configuration, production broker/flag state, production database/state locations, and approved SSH access remain unknown. Do not infer them from the staging profile.

**Decision: production NO-GO.** The local source revision and artifacts are reproducible and content-verified, but the commit is unsigned, no trusted signing key was established, and Linux/Python 3.12 staging, real service behavior, state preservation, backup/restore, and rollback remain unverified.

### Status update to Section 9

Section 9 records the prior phase snapshot, before a reviewed source commit and candidate artifacts existed. Its dirty-worktree provenance blocker is now resolved locally by commit 4448a84786fe88afaf9ea067a0aca4742ba46184 and the content-verified artifacts above. Signature trust remains unverified because the new commit is unsigned. Linux/Python 3.12 staging, actual service behavior, state preservation, backup/restore, and rollback remain blockers; the production decision therefore remains NO-GO.

## 11. WSL staging preparation attempt — 2026-10-17

### Verified in this attempt

- Windows reports build 19045, satisfying the requested WSL build floor. The current PowerShell process is not elevated (Administrator check: false).
- Exact read-only command results: `wsl.exe --status` exited 50 with no output; `wsl.exe --version` exited 1 and printed generic usage; `wsl.exe --list --verbose` exited 1 and printed generic usage. No Ubuntu distribution could be enumerated and no WSL/Linux environment was verified. DISM read-only feature queries for `Microsoft-Windows-Subsystem-Linux` and `VirtualMachinePlatform` each exited 740 (elevation required). These results do not establish whether a distro exists behind a disabled/unavailable WSL feature.
- `Get-Command uv, python3.12, systemd-analyze, docker` found none in the Windows command path. Windows has a Python 3.12 interpreter, but that is not Linux staging evidence.
- Candidate manifest names source commit `4448a84786fe88afaf9ea067a0aca4742ba46184`, tree `0e4a8048ab021207fc3786fee27daaadf35ebafc`, and package version `0.1.0`; it marks the commit unsigned. Recomputed `source.zip` SHA-256 is `4a613bb2cb0ccbe8caa308f5f204e9791d161e2475a74575069b1739e56f7285` (PASS against manifest). Recomputed wheel SHA-256 is `51452930a6d5fc1e13249f0e590647a3e0429fc83d27306bfbb68f70fe5057bd` (PASS). The committed source archive contains `uv.lock`; its SHA-256 `3180c29953f2701e3f222fdc625243776b59091e82689d6e4ce5a0198aec51ef` matches the manifest. The archive path scan found no production `.env` or session files.
- Static candidate config confirms `telegram-ai.service` runs as `enan-ultra`, works in `/opt/enan-ultra`, and may write only `/var/lib/telegram-ai`. Without the staging override, the default relative database `data/telegram_ai.sqlite3` resolves under `/opt/enan-ultra/data`; runtime health marker then resolves beside it, outside the unit's declared writable state root and away from the broker's `/var/lib/telegram-ai/runtime-health.json`. The staging drop-in resets the base EnvironmentFile list, loads `/etc/telegram-ai/staging.env` followed by `/etc/telegram-ai/staging-state.env`, and the staging state example sets `DATABASE_PATH=/var/lib/telegram-ai/telegram_ai.sqlite3`. This statically aligns the database and derived marker with the writable root and broker expectation. No effective systemd configuration was loaded or verified.

### Test and staging status

- The previous Windows/Python 3.15 run remains: ZIP suite 28 passed, deployment suite 22 passed, full suite 181 passed. These are not Linux/Python 3.12 results.
- NOT RUN: `uv sync --locked --python 3.12 --group test`; targeted Linux pytest command; full Linux pytest command; clean Linux wheel installation/import-origin check; `systemd-analyze verify`; effective unit/drop-in or environment precedence checks; disposable SQLite migration/backup/restore/integrity/health checks; simulated activation failure and rollback. No staging files were extracted and no service/socket/broker was started.
- Production remains NO-GO. No Azure state or production service was inspected or changed.

### Operator prerequisite and next actions

1. In elevated PowerShell, run `wsl --status` and `wsl --list --verbose`; if Ubuntu is absent, run the already-approved `wsl --install -d Ubuntu-22.04`. Complete Ubuntu's first-run user setup and restart Windows only if the installer requires it.
2. In Ubuntu, verify `wsl.exe --version`, `python3 --version`, `systemctl --version`, and `uv --version`. Provide a Linux Python 3.12 and uv environment; do not regenerate `uv.lock`. If WSL systemd or required Python/tooling cannot be verified, use a disposable Linux/systemd staging host.
3. Copy only the candidate `source.zip`, wheel, and manifest into a fresh directory under the Linux user's home; compare their SHA-256 values to the manifest before extraction or testing. Never copy or source the repository `.env`, sessions, or database.
4. From that home-directory staging copy, run the locked sync, the two requested targeted suites, and the full suite. Install the candidate wheel in a separate clean Python 3.12 venv and record only its package metadata and import path.
5. Validate copied unit/drop-in syntax without enabling or starting anything. Use disposable state under the staging home for SQLite backup/restore/integrity and health-marker checks, then simulate failed activation and rollback. Record WSL-specific limitations; simulated rollback is not production rollback evidence.

**Decision: production NO-GO.** Candidate artifact hashes are verified, but Linux/Python 3.12 staging, systemd behavior, backup/restore, health checks, and rollback remain unverified.

## 12. WSL IPv4 network diagnosis and candidate offline preflight — 2026-10-17

### Network findings

- Windows build is 19045.6466; WSL reports version 3.0.1.0, kernel 6.18.40.1, Ubuntu-22.04 on WSL 2. `wsl --shutdown` followed by a fresh Ubuntu launch did not restore outbound connectivity.
- Windows `Resolve-DnsName` and TCP/443 checks succeeded for `pypi.org`, `astral.sh`, and `github.com`. Supplemental `curl.exe -4` checks returned HTTP 200 for all three.
- In Ubuntu, the same IPv4 HTTPS probes returned HTTP 000 with connection timeouts for all three hosts. `getent ahostsv4` resolved all three. The WSL default route is via `172.22.176.1`, resolver is `172.22.176.1`, and the WSL adapter is up. Proxy environment variables were unset.
- Windows reports the WSL virtual adapter up, HNS, SharedAccess, vmcompute, LxssManager, and WslService running, default outbound firewall action Allow, and local firewall rule merging enabled. Tailscale is active, but the Windows default route is via Wi-Fi; no evidence identifies Tailscale as the cause. No `.wslconfig` file was present. These facts localize the failure to outbound connectivity from the WSL NAT path or a host policy/filter, but do not identify a single blocking component.
- Microsoft documents WSL 2's default NAT architecture and notes that managed firewall policy can block WSL networking; mirrored networking is documented for Windows 11 22H2 and later, so it is not an applicable fix for this Windows 10 build. See [WSL troubleshooting](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting) and [WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking).

### Candidate artifact and source verification

- Candidate directory: `D:\FTP\Telegram-Bot\release-artifacts\staging-candidate-4448a84786fe\`.
- Manifest source commit `4448a84786fe88afaf9ea067a0aca4742ba46184` and tree `0e4a8048ab021207fc3786fee27daaadf35ebafc` match the local Git object; `HEAD` is the candidate commit. `git status --short --untracked-files=all` showed five pre-existing untracked report/manifest files and no tracked modifications. They were preserved; nothing was staged or committed.
- `source.zip` SHA-256 is `4a613bb2cb0ccbe8caa308f5f204e9791d161e2475a74575069b1739e56f7285`; manifest match: PASS. All 145 archive files were compared against Git blob IDs for the candidate commit: 145 files, 0 path differences, 0 blob mismatches. ZIP check: CRC clean; 0 unsafe paths, symlinks, or duplicate names. The source archive contains `uv.lock`; its SHA-256 is `3180c29953f2701e3f222fdc625243776b59091e82689d6e4ce5a0198aec51ef`, matching the manifest.
- Wheel SHA-256 is `51452930a6d5fc1e13249f0e590647a3e0429fc83d27306bfbb68f70fe5057bd`; manifest match: PASS. Its 54 package files match candidate Git blob IDs (0 mismatches). Metadata: `enan-ultra-telegram-ai` version `0.1.0`, Python requirement `>=3.11,<3.16`. The manifest identifies `v3.0` as a legacy tag, not the package version, and says the local commit is unsigned; no signature was verified.
- The verified `source.zip`, manifest, and wheel were copied to `/home/enan9/enan-ultra-staging-4448a84786fe/`; the archive was safely extracted under its `source/` directory. The original repository and artifacts were not modified. Windows `core.autocrlf=true` explains raw working-file byte differences; direct commit-blob comparisons passed.

### Staging configuration and command results

- Runtime default `DATABASE_PATH=data/telegram_ai.sqlite3` resolves under `/opt/enan-ultra`, outside the service unit's `/var/lib/telegram-ai` writable path. The staging example overrides it to `/var/lib/telegram-ai/telegram_ai.sqlite3`; its parent is writable and the runtime marker resolves to `/var/lib/telegram-ai/runtime-health.json`, matching the broker's expected marker path. The staging drop-in clears the base environment-file list and then references `/etc/telegram-ai/staging.env` and `/etc/telegram-ai/staging-state.env`. Those actual staging-only files do not exist in WSL, so environment-file loading and runtime behavior remain NOT VERIFIED.
- Ubuntu has Python 3.10.12, no `python3.12`, and no `uv`; `systemd-analyze` is version 249 and `systemctl is-system-running` reported `running`. A WSL launch also warned that it could not start the systemd user session for `enan9`.
- `systemd-analyze verify` was run against the candidate service, broker service, and socket without enabling or starting anything. It exited 1: host `netplan-ovs-cleanup.service` could not be opened due to permission, host `snapd.service` uses unknown key `RestartMode` for systemd 249, and candidate `telegram-ai.service` points at `/opt/enan-ultra/.venv/bin/python`, which is not installed in this isolated WSL environment. Therefore unit verification is NOT VERIFIED, not a PASS.
- `uv sync --locked --python 3.12 --group test`, ZIP/deployment tests, full tests, wheel installation/import-origin checks, health checks, database backup/restore, and rollback tests were NOT RUN. Python 3.12/uv and outbound WSL package access are unavailable. Test count for this WSL attempt: 0.

### Next operator actions and gate

1. Have the authorized network or endpoint-security administrator inspect outbound TCP/443 from the WSL virtual network (`vEthernet (WSL)`, current subnet `172.22.176.0/20`) and any explicit firewall, VPN, or endpoint-filter policy. Do not add broad firewall exceptions or disconnect managed security controls without authorization.
2. Re-run the three WSL `curl -4` probes. After they succeed, install Python 3.12 and `uv` in Ubuntu, run the exact locked sync and requested pytest commands, then continue with isolated dummy environment files, service verification, disposable-database restore, health-marker, interruption, and rollback checks.
3. Production remains **NO-GO**. Release hashes and source provenance are verified locally; Linux/Python 3.12 tests, actual staging configuration, backup/restore, and rollback are not verified.

## 13. ENAN ULTRA bug recovery and duplicate update protection — 2026-10-17

This section appends the local bug-fix work to the existing readiness history. No production service, Azure resource, firewall, production database, deployment broker, or `.env` file was accessed or changed.

**Historical note:** The opening snapshot and earlier sections describe a prior direct `/update` implementation. That snapshot is superseded by this section: the current local handler stages reviewed ZIPs without writing to the live source tree or restarting the bot process.

### Confirmed fixes in the working tree

- The default Telegram command list no longer exposes `/update`, `/block`, or `/unblock`; the administrator scopes keep them. Backend checks remain in place, and the ZIP upload/review callbacks now require the exact configured `ADMIN_ID` and `ADMIN_CHAT_ID`.
- Reinstalling one `BotHandlers` instance on the same application does not add handlers twice. A second handler instance on that application is rejected. Each Telegram update ID is claimed atomically in SQLite before handlers run; duplicates and claim-store errors stop before side effects. Old claims are deleted after 30 days.
- The live-tree `/update` extractor and process restart were removed from the Telegram handler. Admin ZIPs are scanned every time, regardless of the general-upload flag; unique quarantine files and SHA-256 metadata are kept when the scanner or archive validation fails. Only validated archives enter the restricted staging manager, where they remain reviewable and the live project tree is not modified. Quarantine entries expire after 24 hours. Production activation was not attempted.
- Scanner failures now carry secret-safe categories for unconfigured/missing/unreachable sockets, timeouts, incomplete replies, and invalid replies. This improves diagnosis; it does not prove ClamAV is running on any target host.
- Automatic model selection prefers a configured Ollama adapter and falls through to the configured cloud order when it fails. Explicit model selection and local-only mode retain their prior behavior.
- Fresh installer configuration rewrites the shipped relative database default to `/var/lib/telegram-ai/telegram_ai.sqlite3`. Relative or out-of-bound database paths are rejected by the installer; a custom path inside the allowed state directory is preserved.

### Verification and evidence

- The final full suite on Windows/Python 3.15 passed: **191 passed in 75.70s** using an isolated locked environment. Full suites before the last caption-routing regression passed with **190 passed** on Python 3.11, 3.12, 3.13, and 3.14; the three final ZIP caption/scan/quarantine regression tests passed on each of those four versions.
- CI quality checks passed locally: Ruff `0.17.1` with the workflow's `E4,E7,E9,F` selection, Python compileall, Mini App JavaScript syntax check, and wheel build.
- One non-isolated matrix attempt accidentally selected the existing Python 3.12 environment without project dependencies and stopped during collection with 16 import errors and zero tests run. The isolated reruns above passed; this did not establish a source test failure.
- ZIP, scanner, provider, authorization, deduplication, installation-idempotency, deployment-path, sticker, and theme tests use local fixtures or mocks. Tests did not call Telegram, ClamAV, production systemd, Azure, or the production deployment broker.
- The GitHub Actions URLs and public jobs API for the prior run supplied in chat returned 404 from this workspace. Its original failure traceback and cause could not be established. New checks may run after the authorized source push.

### Remaining production gates

- Real Linux/systemd validation has not run. The local Python matrix ran on Windows and is not a substitute for the Linux/Python 3.12 staging suite.
- No live ClamAV socket/daemon health was checked. Configure and verify ClamAV on an approved disposable Linux staging host before accepting updates there.
- ZIP approval state remains process-local, and deployment activation/restart is intentionally outside this local work. Staging interruption, a real backup and restore, application health validation, and rollback need a disposable Linux VM and disposable state.
- No live Telegram end-to-end test or browser visual/accessibility test ran. Sticker sending and Mini App rendering remain subject to real client/API verification.

**Decision: production NO-GO.** Local fixes and cross-version Windows tests do not satisfy the remaining Linux/systemd, real scanner, Telegram, state-preservation, backup/restore, and rollback gates.
