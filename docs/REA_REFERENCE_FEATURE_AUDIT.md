# REA reference feature audit

## পরিধি ও প্রমাণের সীমা

২৪ অক্টোবর ২০২৬-এ GitHub default branch-এর static source ও README পর্যালোচনা করা হয়েছে। Reference code clone, dependency install বা execute করা হয়নি। এই সেশনে REA MCP capability উপলভ্য ছিল না; তাই REA Evidence ID বা runtime trace নেই। “সরাসরি দেখা” মানে সোর্সে implementation আছে—runtime-এ চালিয়ে যাচাই করা নয়। “README claim” implementation-এর প্রমাণ নয়। “পরিদর্শিত প্রমাণে পাওয়া যায়নি” পুরো repository-তে অনুপস্থিতির দাবি নয়।

পর্যালোচিত snapshot:

- [Brahma-Ai-Evo](https://github.com/titechprabhasolutions/Brahma-Ai-Evo), commit [`c8d9c0f8f1b14b67baf4c258a15be631793a7381`](https://github.com/titechprabhasolutions/Brahma-Ai-Evo/commit/c8d9c0f8f1b14b67baf4c258a15be631793a7381)
- [Mark-LVI](https://github.com/FatihMakes/Mark-LVI), commit [`4d4035c1b40a3bc796ac63f2f1b6a99bc0dd334d`](https://github.com/FatihMakes/Mark-LVI/commit/4d4035c1b40a3bc796ac63f2f1b6a99bc0dd334d)

## বৈশিষ্ট্য তুলনা

| বৈশিষ্ট্য | Brahma-Ai-Evo প্রমাণ | Mark-LVI প্রমাণ | বাস্তব আচরণ | Telegram bot-এ প্রাসঙ্গিকতা | প্রস্তাব |
|---|---|---|---|---|---|
| Self-analysis ও codebase বোঝা | `actions/auto_heal_engine.py`: `TracebackAnalyzer.parse`, `extract_ast_context`; README-তে traceback repair claim | পরিদর্শিত README ও `core/`/`actions/` entry point-এ project-wide source analysis পাওয়া যায়নি | Brahma traceback থেকে ফাইল/লাইন ও AST context শনাক্ত করে; README-র বিস্তৃত দাবিগুলো runtime-এ পরীক্ষা করা হয়নি | উচ্চ; bounded, read-only analysis নিরাপদ | `/patch`-কে upload ZIP-এ সীমাবদ্ধ রাখা; দরকার হলে allowlisted manifest/import summary যোগ |
| Self-evolution cycle | `core/skill_forge.py`: `SkillForge.forge_skill`; `actions/auto_heal_engine.py`: `AutoHealEngine.heal_traceback` | পরিদর্শিত প্রমাণে পাওয়া যায়নি | Brahma-তে bounded repair attempt ও auto-heal flow সরাসরি আছে | ধারণাগতভাবে প্রাসঙ্গিক; automatic production mutation নয় | proposal → isolated validation → human approval → export |
| নিজের source-code change generation | Brahma `_synthesize_patch_code`, `heal_traceback`; README বিভাগ ৩ | source-code patch generator পাওয়া যায়নি | Brahma replacement chunk তৈরি করে target ফাইলে প্রয়োগ করে | ঝুঁকিপূর্ণ; bot-এর নিজের live files বদলানোর প্রয়োজন নেই | শুধু proposed diff; source archive-এর base ও hash যাচাই |
| Patch/diff তৈরি | Brahma `apply_patch_resiliently` target/replacement chunk ব্যবহার করে | patch workflow পাওয়া যায়নি | সরাসরি দেখা; সাধারণ unified Git diff নয় | উচ্চ | bounded unified diff, path scope, patch/base SHA-256 |
| Isolated patch application | Brahma `SafetySandbox`; `core/skill_crucible.py` subprocess runner | patch application পাওয়া যায়নি | Brahma AST check/backup/child process দেয়; OS/container isolation প্রমাণিত নয় | উচ্চ | rootless, network-disabled disposable container |
| Automated tests ও validation | Crucible `validate_ast`, `run_sandbox_test`; auto-heal `SafetySandbox.validate_code` | provider/action code ও tests directory; patch validation পাওয়া যায়নি | Crucible-এ timeout আছে; test-output JSON parse ব্যর্থ হলে code success fallback দেয়—সরাসরি দেখা, false-positive ঝুঁকি | অত্যন্ত উচ্চ | parse/timeout ব্যর্থতায় fail closed; আলাদা validation ফল |
| ব্যর্থ patch diagnosis/repair proposal | `SkillForge.forge_skill`-এ সীমিত LLM repair retry; traceback analyzer | `core/outcome.py` ব্যর্থতার ফল বর্ণনা করে; code repair পাওয়া যায়নি | Brahma bounded retry সরাসরি আছে; host/dependency isolation অপর্যাপ্ত | মাঝারি | failed candidate অপরিবর্তিত; শুধু bounded repair proposal, auto-apply নয় |
| Skill generation/registration | `core/skill_forge.py`, `core/dynamic_registry.py` | `core/plugin_loader.py` plugin discovery/validation/enable | দুই প্রকল্পেই dynamic loading আছে; Brahma source-এ forge flow আছে, hot-registration দাবি README-তেও আছে | মাঝারি; নতুন plugin system জরুরি নয় | প্রয়োজন প্রমাণ না হওয়া পর্যন্ত যোগ নয় |
| Skill sandboxing | `core/skill_crucible.py`: AST scan, dependency resolver, subprocess | `core/plugin_loader.py` plugin-কে app process-এ import করে | Crucible `pip install` চালায় এবং container boundary দেখায় না; Mark plugin isolation পাওয়া যায়নি | নিরাপত্তার জন্য উচ্চ | arbitrary skill execute নয়; প্রয়োজন হলে পৃথক runner/allowlist |
| Execution reflection/tool outcomes | `core/execution_reflection.py`: `reflect_and_verify` | `core/outcome.py`: `done`, `unverified`, `failed`, `ask`, `started` | Mark-এর result contract সরাসরি; Brahma-র generic default success সব action-এর state যাচাই করে না | উচ্চ | bot-এর `ActionResult`-এর outcome/evidence বজায় রেখে action-specific postcondition |
| Provider routing/fallback | `llm_client.py`, `or_client.py`; README provider claims | `providers/registry.py`, `core/llm_client.py`: lazy provider loading ও local/OpenAI-compatible backend | registry সরাসরি দেখা; সব fallback runtime-এ যাচাই নয় | উচ্চ; bot-এ ইতিমধ্যে routing আছে | বিদ্যমান `ProviderRouter` বজায় রাখা |
| Short/long-term memory | `memory/memory_manager.py`: bounded JSON persistence/search/forget; `core/memory_cortex.py` | `memory/memory_manager.py`: `long_term.json`, remember/forget/session history | উভয় source-এ local JSON memory দেখা; Mark README provider secrets-কে plaintext বলে | উচ্চ | bot-এর encrypted DB, per-user scope, retention/delete বজায় রাখা |
| Tool permissions/execution budgets | Brahma README-তে permission sentry, `agent/executor.py` | Mark `core/plugin_loader.py` enable state; `core/confirm.py` interface-issued confirmation | Mark confirmation irreversible UI actions-এর জন্য সরাসরি; global cost/call budget evidence মেলেনি | উচ্চ | bot-এর admin gate ও call/cost/time limits বজায় রাখা |
| Human approval/rollback | Brahma `SafetySandbox` backup/rollback; `heal_traceback` apply flow | Mark `core/confirm.py`, `core/undo.py` | Brahma source apply-এর আগে human approval দেখা যায়নি; Mark confirmation source patch gate নয় | অত্যন্ত উচ্চ | source release approval ও deployment আলাদা রাখা |
| Voice/STT/TTS | README-তে real-time voice দাবি | README voice claims; `providers/stt_whisper.py`, `providers/tts_system.py` | Mark provider declarations/source দেখা; live behavior চালানো হয়নি | Telegram-এ সীমিতভাবে প্রাসঙ্গিক | bot-এর বর্তমান transcription/voice reply বজায় রাখা |
| Document/research | README-তে vision/desktop claims; এই audit-এ পূর্ণ implementation trace হয়নি | inspected source set-এ সমমানের document/research workflow প্রমাণিত নয় | অযাচাইকৃত বা README-only | উচ্চ; bot-এ বিদ্যমান | বর্তমান media/document/web services বজায় রাখা |
| Scheduling/automation | `agent/task_queue.py`; proactive feature README claim | README-তে proactive claims; Telegram scheduler contract পাওয়া যায়নি | আংশিক; runtime schedule চালানো হয়নি | উচ্চ | bot-এর persisted reminders/recovery ব্যবহার |
| Observability/audit/security | auto-heal patch history/backup; README permission claim | plugin error logging; README plaintext credential disclosure | source-level only; production security posture অযাচাইকৃত | অত্যন্ত উচ্চ | bot-এর encrypted payload, hash-chained audit, secret redaction বজায় রাখা |

## নিরাপত্তা ও লাইসেন্স

- Brahma `actions/auto_heal_engine.py` traceback থেকে replacement তৈরি করে, syntax/AST যাচাইয়ের পর target file-এ লেখে ও backup রাখে; এটি human-approved release workflow নয়।
- Brahma `core/skill_crucible.py`-এর AST keyword filter sandbox boundary নয়। Dependency resolver runtime Python environment-এ `pip install` করে; subprocess-এর network/filesystem/credential isolation প্রমাণিত নয়। JSON output parse ব্যর্থ হলে test pass দেখানোর fallback candidate যাচাইয়ের জন্য অনিরাপদ।
- Mark `core/plugin_loader.py` plugin module import/dispatch করে; `core/confirm.py`-এর UI confirmation arbitrary plugin বা source patch-কে isolate করে না।
- Brahma `LICENSE` personal-use/source-available; distribution ও commercial use নিষিদ্ধ। Mark `LICENSE` CC BY-NC 4.0; commercial use নিষিদ্ধ এবং attribution আবশ্যক। এই প্রকল্পে কোনো source, asset বা substantial text কপি করা হয়নি; কেবল উচ্চ-স্তরের ধারণা স্বাধীনভাবে বিবেচিত হয়েছে। বাণিজ্যিক ব্যবহারের আগে আইনগত পর্যালোচনা প্রয়োজন।
- Brahma `test_self_heal.py` patch/rollback করে; untrusted reference code execute না করার শর্তে এটি চালানো হয়নি।

