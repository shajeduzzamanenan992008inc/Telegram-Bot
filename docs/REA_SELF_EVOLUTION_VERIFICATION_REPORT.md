# Self-evolution end-to-end verification report

## সারাংশ ও সিদ্ধান্ত

Self-evolution-এর proposal → sandbox test → staging approval → candidate export → আলাদা deployment approval পথটি source inspection এবং স্থানীয় focused tests দিয়ে যাচাই করা হয়েছে। ব্যর্থ/বাধাপ্রাপ্ত export delivery-তে audit event হারিয়ে যাওয়ার একটি বাস্তব gap পাওয়া গেছে; cancellation audit করে cancellation পুনরায় raise করার fix ও regression test যোগ হয়েছে।

**Production readiness: NO-GO / staging ও rollback যাচাই অসম্পূর্ণ।** স্থানীয় tests সফল হলেও এই Windows/WSL পরিবেশে rootless Docker daemon বা systemd user bus নেই। তাই বাস্তব sandbox container, Linux deployment account, production-like staging, service recovery বা database restore rehearsal চালানো হয়নি। এই report deployment অনুমোদন নয়।

## Worktree অখণ্ডতা

- Repository: `D:\FTP\Telegram-Bot`; branch `codex/admin-zip-update`; `HEAD` অপরিবর্তিত `1b38014cba8623ca479e880969ec81e28def7cd6`।
- এই পর্যায়ের প্রথম status-এ ২৮টি tracked file modified এবং ৫টি untracked file ছিল; কোনোটিই revert, stage, commit বা delete করা হয়নি। পরের status-এও মোট ৩৩টি status entry ছিল এবং `HEAD` একই।
- এই পর্যায়ে `src/enan_ultra/handlers.py` ও `tests/test_omega_mission_evolution.py`-তে সীমিত পরিবর্তন এবং আগে থেকেই থাকা untracked এই report-এ পরিবর্তন করা হয়েছে। এগুলোর আগের dirty diff-কে এই পর্যায়ের পরিবর্তন বলে গণ্য করা হচ্ছে না।
- কোনো database, production file, live service বা deployment state পরিবর্তন করা হয়নি; কোনো secret report-এ কপি করা হয়নি।

## বাস্তব implementation trace — static source inspection

| ধাপ | বাস্তব আচরণ ও প্রমাণ |
|---|---|
| Patch generation | [`handlers.py`](../src/enan_ultra/handlers.py#L2905) এর `patch_project` user-supplied ZIP থেকে bounded source পড়ে configured model-কে unified diff দিতে বলে। [`code_sandbox.py`](../src/enan_ultra/services/code_sandbox.py#L154) এর `validate_evolution_patch` patch size/encoding, path traversal, protected paths, symlink ও credential heuristic যাচাই করে। |
| Proposal storage | `patch_project` source archive sanitize করে, তারপর [`database.py`](../src/enan_ultra/storage/database.py#L2263) এর `create_evolution_proposal` encrypted payload-এ patch/source রাখে এবং patch ও source SHA-256 সংরক্ষণ করে। Proposal-এর TTL ও retention limit আছে। |
| Isolated test | [`code_sandbox.py`](../src/enan_ultra/services/code_sandbox.py#L252) এর `run_python_patch_tests` Linux POSIX ও rootless Docker বাধ্যতামূলক করে; disposable workspace-এ source কপি, `git apply --check`, `git apply`, `compileall`, pytest চালায়। Container-এ `--network=none`, read-only root, capability drop, no-new-privileges এবং CPU/memory/pid/time bound আছে। `git apply` কেবল ওই ephemeral container workspace-এ। |
| Staging verification ও candidate | [`handlers.py`](../src/enan_ultra/handlers.py#L2534) `_run_evolution_test` stored patch/source hash পুনরায় মেলায়। Staging pass হলে [`database.py`](../src/enan_ultra/storage/database.py#L2400) `record_evolution_staging_verification` candidate bytes ও তাদের hash encrypted payload-এ লেখে; state conditional update-এ বদলায়। [`code_sandbox.py`](../src/enan_ultra/services/code_sandbox.py#L386) candidate archive তৈরির আগে symlink/non-regular file ও file/expanded-size সীমা যাচাই করে। |
| Admin approval | [`database.py`](../src/enan_ultra/storage/database.py#L2370) staging approval-এ admin ID, passing test, exact patch hash, current state ও expiry যাচাই করে। [`handlers.py`](../src/enan_ultra/handlers.py#L2737) `/evolve_approve` staging approval নেয়। |
| Candidate export/integrity | [`handlers.py`](../src/enan_ultra/handlers.py#L2608) `evolve_export` admin/private-chat gate, expiry/state/patch hash, Base64, artifact SHA-256, archive path/secret/size policy যাচাই করে; তারপর একই byte buffer Telegram-এ পাঠায়। সফল delivery-র পরে success audit লেখা হয়। |
| আলাদা production approval | [`handlers.py`](../src/enan_ultra/handlers.py#L2756) `/evolve_deploy_approve` কেবল [`database.py`](../src/enan_ultra/storage/database.py#L2461) এর conditional approval state/audit রেকর্ড করে এবং স্পষ্ট জানায় যে deployment হয়নি। এই handler-এ source write, service restart বা deploy call নেই। Evolution code-এ `git apply`-এর স্থান কেবল `code_sandbox.py`-র disposable runner। |
| Audit | [`database.py`](../src/enan_ultra/storage/database.py#L2560) hash-chained audit event লেখে। Test-এ proposal/test/approval/deployment approval ও export success/failure assertions রয়েছে। |

## Executed test evidence

প্রতিটি command এই workspace-এর `.venv` Python ব্যবহার করেছে। Exit code ও ফল tool output থেকে হুবহু:

| Command | Exit code | ফল |
|---|---:|---|
| `.\.venv\Scripts\python.exe -m pytest tests\test_omega_mission_evolution.py -q` | 0 | `18 passed in 26.21s` |
| `.\.venv\Scripts\python.exe -m pytest tests\test_action_results.py tests\test_omega_mission_evolution.py tests\test_final_upgrade.py tests\test_telegram_zip_updates.py tests\test_telegram_deployment.py tests\test_command_menu.py -q` | 0 | `151 passed in 93.48s` |
| `.\.venv\Scripts\python.exe -m compileall -q src\enan_ultra\handlers.py tests\test_omega_mission_evolution.py` | 0 | Python compile সফল |
| `git diff --check` | 0 | whitespace/error check সফল |

প্রাসঙ্গিক test evidence [`test_omega_mission_evolution.py`](../tests/test_omega_mission_evolution.py#L218) ও [`test_final_upgrade.py`](../tests/test_final_upgrade.py#L301)-এ। নতুন focused cases [`test_omega_mission_evolution.py`](../tests/test_omega_mission_evolution.py#L479) থেকে শুরু:

- Stored patch-এর বদলানো hash test result/approval-এ গ্রহণ হয় না; test failure approval বন্ধ করে; non-admin, wrong hash ও replayed state transition ব্যর্থ হয়।
- Candidate archive-এর hash বদলে গেলে export block হয়; `.env`-যুক্ত unsafe archive এবং size limit ছাড়ানো archive export হয় না।
- Export path-এ প্রকৃত `_gate` non-admin user-কে proposal load/audit/delivery-র আগেই থামায়।
- Telegram document delivery `TelegramError`-এ ব্যর্থ হলে `failed:delivery` audit হয়। Coroutine cancellation-এ নতুন code `failed:delivery_interrupted` audit করে `CancelledError` পুনরায় raise করে।
- Patch traversal/protected path/credential validation, secret file exclusion ও “silently excluded archive member” rejection বিদ্যমান tests-এ আছে।
- Audit chain tampering test [`test_final_upgrade.py`](../tests/test_final_upgrade.py#L301)-এ রয়েছে।

এই tests mock Telegram delivery ও local DB/sandbox workflow boundary-র test; এগুলো live Telegram request, rootless container বা production deployment চালানোর প্রমাণ নয়।

## Failure-case matrix

| অনুরোধকৃত কেস | শ্রেণি | প্রমাণ/সীমা |
|---|---|---|
| Invalid patch | Executed | `test_patch_path_policy_and_sandbox_log_redaction` invalid/traversal/protected/credential cases চালায়; validator source inspected। |
| Changed patch hash | Executed | `test_evolution_hash_gates_admin_approval_and_separate_deploy_gate` wrong hash-এ test/approval প্রত্যাখ্যান যাচাই করে। |
| Changed candidate hash | Executed | নতুন export regression test-এ বৈধ ZIP-এর stored digest বদলে delivery বন্ধ ও `blocked:hash_mismatch` audit যাচাই। |
| Unauthorized user | Executed | DB approval test non-admin reject করে; নতুন export test বাস্তব handler `_gate` দিয়ে non-admin-কে proposal read-এর আগেই reject যাচাই করে। |
| Unsafe archive contents | Executed | Archive validator test excluded `.env` শনাক্ত করে; নতুন export test hash-matching unsafe archive প্রত্যাখ্যান যাচাই করে। |
| Oversized archive | Executed | staging DB oversized candidate reject test আগে থেকেই ছিল; নতুন export test size limit অতিক্রম করা ZIP delivery-র আগেই block ও audit যাচাই করে। |
| Failed tests | Executed | Non-zero test result proposal-কে failed করে এবং পরবর্তী approval gate বন্ধ রাখে। |
| Interrupted export | Executed | নতুন `CancelledError` test cancellation propagate হওয়া ও failure audit দুটোই যাচাই করে। |
| Concurrent/replayed approval | Executed | DB test conditional state transition/replay-এ দ্বিতীয় approval ব্যর্থ হওয়া যাচাই করে। |
| Real Linux sandbox / rootless Docker | Blocked | Windows host/WSL distro-তে Docker executable/daemon নেই; নিচে পরিবেশ ফল। Container isolation বাস্তবে চালানো হয়নি। |
| Linux service rollback ও SQLite restore rehearsal | Documented, not executed | deployment guide-এ recovery/backup নিয়ম আছে; live অথবা disposable Linux staging rehearsal হয়নি। |

## Environment ও deployment/rollback readiness

- Host Windows (`Windows_NT`); WSL Ubuntu-22.04 kernel পাওয়া গেছে। WSL probe-এ `docker` command পাওয়া যায়নি। `systemctl` executable থাকলেও `Failed to connect to bus: No such file or directory`; WSL startup-ও systemd user session start failure জানিয়েছে।
- Probe command: `wsl.exe -d Ubuntu-22.04 --exec bash -lc 'printf "uid="; id -u; printf "kernel="; uname -sr; printf "docker="; command -v docker || true; printf "systemctl="; command -v systemctl || true; if command -v docker >/dev/null 2>&1; then docker info --format "{{json .SecurityOptions}}" 2>&1; fi; if command -v systemctl >/dev/null 2>&1; then systemctl is-system-running 2>&1 || true; fi'`. Shell exit code 0 হলেও সেটি `|| true`-র কারণে; Docker/systemd প্রস্তুত ছিল না।
- `docs/DEPLOYMENT_AND_ROLLBACK.md` rootless staging, deployment broker, immutable release, health check/rollback এবং SQLite online backup/restore নির্দেশনা দেয়। সেগুলো নির্দেশিত; এই পর্যায়ে চালানো হয়নি, তাই rollback readiness **unverified**।
- Evolution candidate ZIP source artifact; production database, `.env`, user data, systemd state বা verified recovery snapshot নয়। Production-এ candidate apply করতে documented operator release/review process আলাদাভাবে প্রয়োজন।

## পরবর্তী Linux staging অনুরোধের read-only যাচাই

- সর্বশেষ report ও [`DEPLOYMENT_AND_ROLLBACK.md`](./DEPLOYMENT_AND_ROLLBACK.md) পুনরায় পড়া হয়েছে। এই turn-এর শুরুতে Git status-এ আগের মতো 28 modified tracked file ও 5 untracked file (33 status entry) ছিল; `HEAD` `1b38014cba8623ca479e880969ec81e28def7cd6`। কোনো পুরোনো পরিবর্তন সরানো হয়নি।
- Windows tool probe: `docker=unavailable`, `systemctl=unavailable`; `ssh.exe` ও `wsl.exe` পাওয়া গেছে, কিন্তু ব্যবহারকারীর পক্ষ থেকে কোনো অনুমোদিত Linux staging host/connection দেওয়া হয়নি। SSH থাকাই host access বা অনুমতির প্রমাণ নয়।
- WSL distribution probe-এ `Ubuntu-22.04` পাওয়া গেছে। read-only command `wsl.exe -d Ubuntu-22.04 --exec bash -lc 'printf "uid="; id -u; printf "docker="; command -v docker || true; printf "systemctl="; command -v systemctl || true; if command -v systemctl >/dev/null 2>&1; then systemctl is-system-running 2>&1 || true; fi; printf "docker_socket="; ls -l /run/user/$(id -u)/docker.sock 2>&1 || true; printf "docker_host="; printf "%s" "${DOCKER_HOST-unset}"; printf "\n"'` shell exit code `0`; output-এ `uid=1000`, docker command অনুপস্থিত, systemctl bus `Failed to connect to bus: No such file or directory`, `/run/user/1000/docker.sock` অনুপস্থিত, `DOCKER_HOST=unset`; startup-এ systemd user session failure warning। `|| true` থাকার কারণে exit `0` prerequisite সফলতার প্রমাণ নয়।
- কোনো package install, WSL configuration change, service enable/start, system setting change, production connection বা database access করা হয়নি।

### Staging-এর নির্দিষ্ট prerequisites

1. **অনুমোদিত, production থেকে বিচ্ছিন্ন Linux host**: staging owner ও hostname/connection স্পষ্টভাবে অনুমোদিত; production filesystem, database, `.env`, bot token, provider credentials, systemd unit/socket ও broker credentials থেকে আলাদা। এই host-এ কোনো production secret বা production DB কপি করা যাবে না।
2. **Sandbox runner-এর জন্য service-account rootless Docker**: Linux/POSIX runtime-এ Docker CLI এবং dedicated unprivileged runner user-এর নিজস্ব চলমান rootless daemon; ওই user-এর `docker info --format '{{json .SecurityOptions}}'` output-এ `rootless` থাকতে হবে। `DOCKER_HOST`/`XDG_RUNTIME_DIR` দিয়ে সেই user-এর socket-এ সংযোগ থাকতে হবে। Configured sandbox image আগেই স্থানীয়ভাবে উপস্থিত থাকতে হবে, কারণ runner `--pull=never` ব্যবহার করে; image pull/install-এর জন্য আলাদা অনুমোদন প্রয়োজন।
3. **Disposable candidate-only input**: test ZIP, patch, sandbox scratch/state directory ও keys/credentials-বিহীন ন্যূনতম environment; production DB, production `.env`, deploy socket, release directory বা broker authorization DB container-এ mount করা যাবে না। Runner source ও patch readonly mount করে এবং container-এর network `none` করে; এই সুরক্ষা host permission/scratch isolation-এর বিকল্প নয়।
4. **Rollback rehearsal-এর আলাদা systemd test harness**: disposable VM/host-এ systemd manager/user bus সচল; isolated test service এবং আলাদা state/database ও throwaway credentials; service identity, path, unit ও broker configuration পর্যালোচিত। Production service name, production database ও production deployment broker ব্যবহার না করে interrupted activation/health-failure/recovery পরীক্ষা করতে হবে।
5. **Recovery evidence**: শুধুমাত্র staging DB-তে SQLite online backup, backup digest ও `PRAGMA integrity_check`; আলাদা copy-তে restore/health validation; candidate activation ব্যর্থতা এবং rollback ব্যর্থতার ফল/journal। কোনো production migration/deploy/restart নয়।

### Blocker ও নিরাপদ setup পরিকল্পনা

- **বর্তমান blocker:** এখানে অনুমোদিত Linux target নেই; বিদ্যমান WSL-এ Docker CLI/rootless daemon/socket নেই এবং systemd user bus সচল নয়। ফলে rootless sandbox বা systemd rollback harness চালানো সম্ভব হয়নি।
- **এ পর্যায়ে করা যাবে না:** WSL systemd enable/configure, apt/package install, Docker/rootless daemon setup, host service enable/start বা remote host guess করে SSH করা—এসব user/system setting বদলায় বা অননুমোদিত target access; স্পষ্ট অনুমতি ছাড়া করা হয়নি।
- **পরবর্তী ধাপ:** user একটি disposable Linux staging host ও access scope অনুমোদন/নির্দেশ করবেন; host administrator সেখানে আলাদা staging account, rootless Docker, systemd test unit, locally pinned sandbox image, secret-হীন test fixture, disposable DB/state directory প্রস্তুত করবেন। Preparation complete হলে runtime prerequisites read-only যাচাই করে শুধু staging fixture-এ isolated test, failure recovery, backup/restore ও rollback চালানো যাবে। সেই ফল না আসা পর্যন্ত production readiness **NO-GO** থাকবে।

## Documented but not executed

- Linux service account-এর rootless Docker daemon-এ configured sandbox image দিয়ে আসল patch apply/test/export; network বিচ্ছিন্নতা, timeout-kill, temporary workspace cleanup।
- Intended non-production Linux deployment host-এ staging candidate integrity check, operator approval, systemd health check/rollback drill।
- আলাদা staging state directory-তে SQLite online backup/restore ও `PRAGMA integrity_check` rehearsal।
- Live Telegram transport delivery/cancellation timing এবং provider/model-এ ZIP source পাঠানোর আচরণ। কোনো live API call এই পর্যায়ে করা হয়নি।

## অবশিষ্ট নিরাপত্তা ঝুঁকি

- Patch secret detection path/credential heuristic; এটি সব secret শনাক্ত করার নিশ্চয়তা নয়। ZIP sanitization secret-নাম/নির্দিষ্ট extension বাদ দেয়, কিন্তু গোপন তথ্যের যেকোনো রূপ শনাক্ত করার নিশ্চয়তা দেয় না।
- `/patch`-এর source configured model/provider-এ পাঠানো হতে পারে; local-only profile ব্যবহার না করলে provider-এর data handling policy প্রযোজ্য। Provider execution এই verification-এ করা হয়নি।
- Rootless Docker image provenance, Linux host hardening, bot service account permissions, broker enablement ও production secret isolation এখানে যাচাই হয়নি।
- Cancellation audit failure-এ cancellation ইচ্ছাকৃতভাবে আবার raise হয়; database/audit store নিজেই unavailable হলে successful audit লেখা নিশ্চিত করা যায় না এবং সেটি live fault-injection দিয়ে পরীক্ষা করা হয়নি।
- Static source inspection ও passing local test production correctness, exploit-free status বা deployment safety প্রমাণ করে না।

## পরবর্তী নিরাপদ ধাপ

Production থেকে আলাদা Linux staging VM/container host-এ dedicated unprivileged bot account, rootless Docker daemon, pinned sandbox image, disposable test archive এবং production credentials ছাড়া একই proposal→test→approval→candidate export flow চালান। Candidate/source SHA-256 ও audit chain মিলিয়ে দেখুন; তারপর আলাদা staging state directory-তে database restore ও rollback rehearsal সম্পন্ন করুন। এগুলো পাস না হওয়া পর্যন্ত production release/deployment অনুমোদন দেবেন না।

## Reference scope

এই পর্যায়ের অনুরোধে `Brahma-Ai-Evo` URL একই দুইবার দেওয়া হয়েছিল; এটি দুটি পৃথক reference নয়। আগের static-source সীমা, snapshot ও উচ্চ-স্তরের তুলনা [`REA_REFERENCE_FEATURE_AUDIT.md`](./REA_REFERENCE_FEATURE_AUDIT.md)-এ আছে; project gap matrix [`REA_PROJECT_FEATURE_GAP.md`](./REA_PROJECT_FEATURE_GAP.md)-এ। Reference repository code চালানো হয়নি, এবং এই end-to-end verification-এ আলাদা REA binary/runtime Evidence ID তৈরি হয়নি।
