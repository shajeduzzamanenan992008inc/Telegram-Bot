# REA project feature-gap analysis

## Baseline

এই audit বর্তমান checkout-এর read-only পরিদর্শন ও focused test run-এর ওপর ভিত্তি করে। Branch `codex/admin-zip-update`, `HEAD` `1b38014cba8623ca479e880969ec81e28def7cd6`। পরিদর্শনের সময় ২৮টি tracked file-এ unstaged পরিবর্তন এবং ২টি untracked test ছিল; এগুলো baseline work, এই কাজের আগে থেকেই ছিল। REA MCP connection ছিল না। Reference audit: [REA_REFERENCE_FEATURE_AUDIT.md](./REA_REFERENCE_FEATURE_AUDIT.md)।

## Gap matrix

| বৈশিষ্ট্য | শ্রেণি | বর্তমান প্রমাণ | নির্ভরতা/ঝুঁকি | প্রয়োজনীয় পরীক্ষা | অগ্রাধিকার |
|---|---|---|---|---|---|
| Read-only codebase understanding | আংশিক | `services/code_sandbox.py`: `read_project_sources`; `handlers.py`: `patch_project`; scope হলো ব্যবহারকারী-দেওয়া ZIP, নিজের live checkout নয় | ZIP-এ secret বাদ দেওয়া জরুরি; REA সংযোগ অনুপস্থিত | source allowlist, secret exclusion, bounded file/character count | P2 |
| Evolution proposal cycle | বিদ্যমান ও যাচাইকৃত | `handlers.py`: `/evolve_mode`, `/patch`, review/test/approval; `storage/database.py`: encrypted proposal ও conditional state transition | sandbox configuration ছাড়া proposal pass/approve হয় না; বর্তমান system state dirty | state/hash/expiry/concurrent approval পরীক্ষা | P0 |
| Own-source modification | প্রযোজ্য নয় (ইচ্ছাকৃত সীমা) | `/patch`-এ source ZIP ব্যবহারকারী upload করে; running bot source write করে না | auto-apply যোগ করলে production mutation ঝুঁকি | কোনো production apply path না থাকা যাচাই | P0 |
| Patch/diff generation | বিদ্যমান ও যাচাইকৃত | `handlers.py`: `patch_project`; `services/code_sandbox.py`: `validate_evolution_patch` | generated diff untrusted; protected path ও credential screening heuristic মাত্র | malformed diff, traversal, secret, protected path | P0 |
| Isolated patch application | আংশিক | `services/code_sandbox.py`: rootless Docker, `--network=none`, readonly input, bounded CPU/memory/time; candidate copy-তে `git apply` | কেবল Linux + configured rootless Docker; Windows development host-এ runtime runner চালানো যায় না | real deployment host-এ controlled staging, timeout, container teardown | P0 |
| Syntax/unit/regression validation | আংশিক | sandbox `compileall` ও `pytest`; repo-র tests-এ failure/hash/archive cases | lint/type/security scan, পূর্ণ candidate baseline ও production-like tests সম্পূর্ণ নয় | focused tests; full suite; unavailable check স্পষ্ট রিপোর্ট | P1 |
| Failed-patch diagnosis/repair proposal | অনুপস্থিত | test output proposal-এ সংরক্ষিত; bot নিজে repair draft loop চালায় না | auto-repair uncontrolled mutation-এ পরিণত হতে পারে | failure evidence ও repair proposal আলাদা, bounded retry, no auto-apply | P2 |
| Generated skill registry | অনুপস্থিত | `actions.py`-এ fixed action registry; user-generated skill manifest/registry নেই | arbitrary Python plugin execute করার ঝুঁকি | কোনো dynamic code execution নেই তা নিশ্চিত করা | P3 |
| Skill-specific sandbox | প্রযোজ্য নয় | skill generation/registration নেই; পৃথক Python sandbox আছে | sandbox-কে skill sandbox ধরে ভুল নিরাপত্তা দাবি করা যাবে না | deployment host-এ runner hardening যাচাই | P3 |
| Tool execution outcome contract | আংশিক | `models.py`: `ActionResult`-এ `done/unverified/failed`, operation ID, diagnostic, verification evidence; `actions.py`: metadata persistence | সব integrations-এর post-condition বাস্তবভাবে যাচাই হয় না | false-success, timeout, replay ও failure-output tests | P1 |
| Post-execution reflection | আংশিক | `ActionResult` evidence field আছে; general autonomous reflection/repair service নেই | external side-effect-এর ফল API acknowledgement-এ সীমাবদ্ধ হতে পারে | প্রত্যেক action-এর success criteria আলাদা পরীক্ষা | P2 |
| Provider routing/fallback | বিদ্যমান ও যাচাইকৃত | `services/providers.py`: candidate chain, provider attempt budget, circuit breaker, fallback policy | external provider behavior ও live quota এই task-এ পরীক্ষা করা হয়নি | existing provider reliability tests | P1 |
| Conversation/durable memory & retention | বিদ্যমান ও যাচাইকৃত | `storage/database.py`: encrypted memory, user-scoped retrieval/delete; docs-এ retention/privacy | retention/deletion semantics সহজে regress করতে পারে | privacy export/delete, per-user access, encryption tests | P1 |
| Permissions ও execution budget | বিদ্যমান ও যাচাইকৃত | `handlers.py`: `_gate`; `actions.py`: approval/admin checks; `mission_control.py`: step/cost/worker cap | admin chat এবং private operation distinction বজায় রাখতে হবে | admin/non-admin, owner, call/cost limits | P0 |
| Human approval ও separate deployment state | বিদ্যমান ও যাচাইকৃত | DB state: `awaiting_approval → approved_staging → awaiting_deployment_approval → deployment_approved`; handler message deploy করে না | approval record deployment নয়; staging candidate verified না হলে export করা যাবে না | concurrent/duplicate approvals ও deploy non-effect | P0 |
| Candidate export/audit | আগে আংশিক, এই পরিবর্তনে শক্তিশালী | `handlers.py`: `evolve_export`; `database.py`: encrypted payload; hash/archive validation | expiration cleanup-এর আগে expired export race; rejected/success delivery audit আবশ্যক | expiry, tamper, archive, private chat, delivery failure | P0 |
| Voice/STT/TTS | বিদ্যমান | `services/media.py`: bounded transcription; `services/voice.py`: async TTS | provider/network/dependency availability | existing media route tests | P2 |
| Document/research | বিদ্যমান | `services/document_export.py`, `services/web_reader.py`, `services/web_search.py`, media ingestion | uploaded content untrusted; size/URL policies | existing media/security tests | P2 |
| Scheduling/automation | বিদ্যমান | `handlers.py`: reminders; `runtime.py`: due-reminder claim, delivery retry | duplicate delivery ও persistence recovery | reminder idempotency/expiry tests | P2 |
| Audit/observability/security | বিদ্যমান ও যাচাইকৃত | `database.py`: hash-chained audit; sandbox output redaction; encrypted proposal | audit success/failure semantics ও secret leakage regressions | chain verification, redaction, delivery outcome | P0 |
| Rollback/recovery | আংশিক | candidate export ও ZIP deployment staging; operator docs rollback নির্দেশনা | candidate ZIP সম্পূর্ণ production backup নয়; DB/config/persistent data বাদ | restore rehearsal আলাদা authorized environment-এ | P1 |

## এই কাজের মধ্যে করা সুরক্ষা সংশোধন

- সাধারণ `/patch` ও সব evolution admin command এখন application gate-এই private chat চায়; group chat-এ diff বা proposal state বের হওয়ার আগেই থামে।
- `/evolve_export` proposal-এর timestamp নিজে পরীক্ষা করে; periodic expiry cleanup-এর অপেক্ষায় থাকে না।
- Export audit event এখন success এবং policy/hash/archive/delivery failure আলাদা করে চিহ্নিত করে। Document delivery error হলে সফল export দেখায় না।
- কোনো candidate production source-এ apply, deploy, বা service restart করা হয়নি।

## অবশিষ্ট কাজ

1. Linux deployment-এর configured rootless Docker sandbox-এ বাস্তব isolated run ও timeout/cleanup পরীক্ষা—এই Windows checkout-এ করা হয়নি।
2. Candidate validation-এ lint, type-check, dependency/security scan এবং সম্পূর্ণ release provenance সংযোজন।
3. Read-only current-repository code map (imports/tests/config-এর allowlisted summary) যোগ করা—শুধু বাস্তব প্রয়োজন ও secret-stripping tests প্রমাণিত হলে।
4. Rollback procedure-এর non-production restore rehearsal; archive export-কে production backup বলা যাবে না।
5. REA MCP capability যুক্ত হলে শুধু codebase/static analysis ব্যবহার করে reproducible evidence তৈরি; এই task-এ REA setup বা configuration বদলানো হয়নি।

