# Enan Ultra Telegram AI Assistant

Enan Ultra একটি modular, async Telegram AI assistant। এটি configured AI provider ব্যবহার করে; “GPT-6 Astra” কেবল conversational style profile, বাস্তবে চালু model-এর পরিচয় নয়। Bot নিজেকে AI বলে পরিচয় দেয়, মানুষ সেজে কথা বলে না।

## কী আছে

- বাংলা default, multilingual reply, conversation context, Study Mode, user-controlled voice/media preferences, opt-in memory এবং cost/rate limits।
- Adaptive Living Theme Engine: English, বাংলা এবং নির্বাচিত Roman Bangla topic detection, persistent per-user theme/motion/appearance settings, manual `/theme` controls এবং optional Telegram Mini App-এ streaming chat। এটি deterministic keyword/context matching; typo-heavy বা ambiguous input-এ ভুল হতে পারে। Mini App চালু করতে HTTPS domain ও reverse proxy দরকার; details [`docs/MINI_APP.md`](docs/MINI_APP.md)।
- OpenRouter, Groq, Gemini, Cloudflare Workers AI ও Hugging Face Inference API providers; ডিফল্ট cloud failover ক্রম OpenRouter → Groq → Gemini → Cloudflare → Hugging Face, `AI_PROVIDER_ORDER` দিয়ে বদলানো যায়। Comma-separated keys round-robin শুরু করে; 401-এ rejected key quarantine করে পরের key চেষ্টা হয়, আর account/project quota বা 429-এ sibling key চেষ্টা না করে cooldown মানা হয়। Local-only privacy mode-এ cloud fallback হয় না; সেই mode কেবল configured local Ollama ব্যবহার করে।
- ছবি, screenshot, supported document/audio/video বিশ্লেষণ; file adapters, OCR, QR/barcode, source locators, citation-সহ web search, image generation, transcription ও voice reply। Unsupported format-কে সফলভাবে পড়েছে বলে দাবি করে না।
- Complex research/code/security প্রশ্নে budget-limited specialist review। Specialist agent পরামর্শ দেয়; Telegram/HA/VM action execute করে না। Feedback থেকে repeated issue category-র manual improvement suggestion আসে; prompt বা model নিজে বদলায় না, model train হয় না।
- Encrypted, user-isolated Personal Knowledge Base; user `/kb save` দিয়ে সম্মতি দিলে তবেই স্থায়ী হয়। Per-user document/size cap থাকে; temporary file index নির্ধারিত সময়ে মুছে যায়।
- Loopback-এ private Mission Control dashboard, provider/usage/job/approval/audit view, sanitized in-memory app logs, allowlisted Home Assistant এবং confirmed bot-service restart। Arbitrary shell, reboot বা package update endpoint নেই।
- Admin dashboard-এ disk/RAM availability ও health snapshot থাকে; Linux-এ load average দেখায়, Windows-এ unsupported memory/load values unavailable হিসেবে থাকে। Disk খালি 1 GiB-এর নিচে বা RAM ব্যবহার 95% ছাড়ালে admin-কে ছয় ঘণ্টায় সর্বোচ্চ একবার alert পাঠায়।
- Ubuntu installer ClamAV daemon ও signature updater স্থাপন করে; deployed bot-এ Telegram upload AI processing-এর আগে scan হয়, detection হলে admin alert যায় এবং scanner ব্যর্থ হলে unscanned upload বন্ধ থাকে। বিস্তারিত: [`docs/DEPLOYMENT_AZURE_VM.md`](docs/DEPLOYMENT_AZURE_VM.md)।
- Project ZIP থেকে source review ও downloadable patch। Test শুধু user confirmation-এর পরে rootless, network-বিহীন, disposable container-এ চলে; patch live code-এ apply হয় না।
- Admin-only `/update` flow-তে ZIP validation, isolated staging, SHA-256 file manifest, sandbox validation, আলাদা staging approval এবং (শুধু operator enable করলে) root-owned সীমাবদ্ধ broker-এর মাধ্যমে আলাদা production approval/deploy/restart/health-check/rollback আছে। `UPDATE_DEPLOYMENT_ENABLED` default `false`; installer enable flag তৈরি করে না। বর্তমান Azure VM-এর service user/path/entry point broker-এর অনুমোদিত layout-এর সঙ্গে মেলে না, তাই তার deployment preflight BLOCKED থাকবে যতক্ষণ না operator layout মেলান। কোনো production readiness বা live recovery drill দাবি করা হচ্ছে না। Details: [`docs/TELEGRAM_ZIP_UPDATES.md`](docs/TELEGRAM_ZIP_UPDATES.md), [`docs/DEPLOYMENT_AND_ROLLBACK.md`](docs/DEPLOYMENT_AND_ROLLBACK.md), [`docs/UPDATE_SECURITY.md`](docs/UPDATE_SECURITY.md), [`docs/UPDATE_TROUBLESHOOTING.md`](docs/UPDATE_TROUBLESHOOTING.md)।
- `/privacy export` user-owned stored data JSON-এ দেয়; `/privacy delete confirm` account-এর user-linked data মুছে দেয়। Private chat-এর history/docs export/delete হয়; shared group history অন্য সদস্যদের সুরক্ষার জন্য account action-এ ছোঁয়া হয় না।
- Admin `/block USER_ID [কারণ]` ও `/unblock USER_ID` দিয়ে bot-local moderation করতে পারেন। `/remind 10m daily বার্তা` বা `weekly` দিয়ে recurring reminder তৈরি করা যায়।
- Per-user দৈনিক request limit-এর পাশাপাশি `USER_MONTHLY_REQUEST_LIMIT` ও `USER_MONTHLY_COST_LIMIT_USD` দিয়ে account-level monthly budget সীমা নির্ধারণ করা যায়; `/usage` daily ও monthly consumption দেখায়।
- `/study card add প্রশ্ন :: উত্তর`, `/study due`, `/study review ID good|again`-এ ব্যক্তিগত flashcard ও সহজ spaced-review interval আছে; card শুধু private chat-এ ব্যবহৃত হয়। `/study quiz বিষয়` AI-generated MCQ-কে Telegram-এর native interactive Quiz Poll হিসেবে পাঠায়।
- `/temporary on` private chat-এর পরবর্তী AI কথোপকথন history, memory, document বা personal-KB retrieval ছাড়া চালায়; `/temporary off` স্বাভাবিক persistence ফেরায়। সাময়িক uploaded document-এর বিদ্যমান retention limit আলাদা ভাবে প্রযোজ্য।
- Sticker catalog মূল async SQLite database-এ থাকে এবং scheduled encrypted database backup-এ অন্তর্ভুক্ত। Collection default-off; কেবল configured admin private chat-এ `/collect_on` দিলে admin-দের পাঠানো sticker metadata pending review-তে যায়। `/import_sticker_set <short_name>` Bot API দিয়ে pack metadata আনে; duplicate unique ID এড়িয়ে file ID, pack, type, dimensions, emoji/category suggestion, tags, approval ও usage রাখে। `/sticker_review`, `/sticker_categorize`, `/sticker_search`, `/sticker_stats`, `/sticker_export` দিয়ে catalog পরিচালিত হয়। Sticker emoji শুধু category suggestion—অর্থের নিশ্চয়তা নয়। পুরোনো `stickers_store.db` থাকলে startup-এ metadata মূল database-এ pending review হিসেবে migrate হয়। Guides: [`docs/STICKER_COLLECTION.md`](docs/STICKER_COLLECTION.md), [`docs/STICKER_DATABASE.md`](docs/STICKER_DATABASE.md), [`docs/STICKER_IMPORT.md`](docs/STICKER_IMPORT.md), [`docs/STICKER_CATEGORIZATION.md`](docs/STICKER_CATEGORIZATION.md), [`docs/STICKER_TROUBLESHOOTING.md`](docs/STICKER_TROUBLESHOOTING.md)।

Theme, multimodal routing, voice, document processing, personal KB, research, provider capability, accessibility ও data retention-এর implementation guide: [`docs/ADAPTIVE_THEME_ENGINE.md`](docs/ADAPTIVE_THEME_ENGINE.md), [`docs/MULTIMODAL_ARCHITECTURE.md`](docs/MULTIMODAL_ARCHITECTURE.md), [`docs/VOICE_INTELLIGENCE.md`](docs/VOICE_INTELLIGENCE.md), [`docs/DOCUMENT_PROCESSING.md`](docs/DOCUMENT_PROCESSING.md), [`docs/KNOWLEDGE_BASE.md`](docs/KNOWLEDGE_BASE.md), [`docs/WEB_RESEARCH.md`](docs/WEB_RESEARCH.md), [`docs/PROVIDER_CAPABILITIES.md`](docs/PROVIDER_CAPABILITIES.md), [`docs/PRIVACY_AND_FILE_RETENTION.md`](docs/PRIVACY_AND_FILE_RETENTION.md)।
- BotFather-এ inline mode enable করার পর `@BOT_USERNAME <keyword>` শুধু approved sticker metadata থেকে সর্বোচ্চ ২০টি match দেয়; automatic sticker-এর জন্য user preference, intent/category ও cooldown-ও প্রযোজ্য। `/make_sticker` দিয়ে photo caption বা photo-র reply থেকে static WebP sticker বানানো যায়। `/voice on` voice reply চালু করে এবং confirmation-টিও voice note হিসেবে পাঠায়; configured `TTS_MODEL` থাকলে OpenRouter TTS আগে ব্যবহৃত হয়, না হলে/ব্যর্থ হলে gTTS text Google-এর external service-এ পাঠায়। Generated MP3 পাঠানোর পর temporary file সরানো হয়।
- `/persona default|expert|creative|research|tutor|professional|debate` ব্যক্তিগত উত্তর-ধরন বদলায়; `/persona custom নির্দেশনা` সর্বোচ্চ 500 অক্ষরের user preference সংরক্ষণ করে এবং system/safety rules-এর অধীন থাকে।
- `/timezone Asia/Dhaka` IANA timezone যাচাই করে সংরক্ষণ করে। `/remind YYYY-MM-DD HH:MM বার্তা` ঐ timezone-এর local time-এ একবারের reminder সেট করে; `/reminders` local time-এ দেখায়, `/reminders pause ID|resume ID` দিয়ে pause/resume এবং `/snooze ID 10m` দিয়ে pending reminder পিছিয়ে দেওয়া যায়। Delivery atomic claim ও bounded exponential retry ব্যবহার করে; relative ও daily/weekly recurrence-ও সমর্থিত।
- `/tasks add priority=high due=2026-12-31 after=ID লেখা` task-এ priority, optional ISO-date deadline ও prerequisite যোগ করে; prerequisite শেষ না হলে task complete করা যায় না। `/tasks plan <goal|chat>` বাজেট-সীমাবদ্ধ, privacy-aware AI request থেকে সর্বোচ্চ ১০টি private task ও dependency তৈরি করে; `chat` সাম্প্রতিক bounded private history ব্যবহার করে (temporary chat-এ নয়)। কোনো message-এ reply করে `/tasks add` পাঠালে message text/caption থেকে task তৈরি হয়।
- `/study quiz বিষয়` একটি AI-generated multiple-choice প্রশ্নকে Telegram native interactive Quiz Poll-এ পাঠায়; `/translate bn|en|ar|hi|ur|es|fr|de|ja|zh text` নির্দিষ্ট ভাষায় অনুবাদ করে। দুটোই user-এর privacy mode ও usage limits অনুসরণ করে।
- `/format text|markdown|json` chat উত্তরের কাঙ্ক্ষিত কাঠামো সেট করে; JSON mode model-এর prompt-ভিত্তিক নির্দেশনা, কঠোর JSON schema validator নয়।
- AI answer ও admin support reply একই shared formatter ব্যবহার করে। ব্যক্তিগত chat-এ Bot API Rich Message live preview, native Thinking placeholder, streamed text এবং supported client-এ Stop button চালু; final answer স্থায়ী rich message হিসেবে পাঠানো হয়। Rich API অনুপলব্ধ হলে সাধারণ message draft/text fallback চলে।
- `/export md|txt|pdf` ব্যক্তিগত chat-এর সংরক্ষিত history export করে। PDF-এ বাংলা shaping-এর জন্য Ubuntu installer `fonts-noto-core` যোগ করে; Windows-এ Nirmala UI font ব্যবহার হয়।

## Local setup

Python 3.11–3.15 এবং `uv` ব্যবহার করুন (Python 3.11 সর্বনিম্ন সমর্থিত সংস্করণ)।

```powershell
python scripts/init_env.py
```

`.env`-এ Telegram token, numeric admin chat/user ID এবং অন্তত একটি supported provider configure করুন। Provider key-র জন্য `OPENROUTER_API_KEYS`, `GROQ_API_KEYS`, `GEMINI_API_KEYS`, `CLOUDFLARE_API_TOKENS` ও `HUGGINGFACE_API_KEYS` ব্যবহার করা যায়; plural variable ফাঁকা থাকলে সংশ্লিষ্ট singular `*_API_KEY` fallback হিসেবে ব্যবহৃত হয়। Cloudflare-এর legacy `CLOUDFLARE_API_KEYS` alias-ও চলে। Groq, Gemini, Cloudflare ও Hugging Face-এর জন্য সংশ্লিষ্ট `*_MODEL` configure করুন; Cloudflare Workers AI-এর জন্য `CLOUDFLARE_ACCOUNT_ID` লাগে। Hugging Face endpoint defaults to `https://router.huggingface.co/v1`; সেটি `HUGGINGFACE_BASE_URL` দিয়ে বদলানো যায়। 401-এ rejected key quarantine হয়; 402/429-এ sibling key চেষ্টা হয় না এবং cooldown মানা হয়। সব provider ব্যর্থ হলে bot পরিষ্কার, generic retry-later error দেয়। পুরনো OpenAI, Anthropic, Together AI ও SambaNova credentials আর cloud routing-এ ব্যবহৃত হয় না। Local-only privacy mode-এর জন্য আলাদাভাবে local Ollama configure করা যায়। `APP_DATA_KEY` ও `DASHBOARD_AUTH_TOKEN`-কে `init_env.py` cryptographically secure random value দিয়ে তৈরি করে। `.env` Git-এ tracked নয়; Linux-এ mode `0600`।

Image/voice configuration OpenRouter-এর current catalogs থেকে model ID চায়: `VISION_MODEL` (default `google/gemini-2.5-flash:free`), `IMAGE_MODEL` (image-output model), `TTS_MODEL` (audio-output model), এবং ঐচ্ছিক provider-specific `TTS_VOICE`। `GOOGLE_AI_STUDIO_KEY`-এর বদলে `GEMINI_API_KEY` ব্যবহার করুন; Cloudflare-এ token variable হলো `CLOUDFLARE_API_KEY`, সঙ্গে `CLOUDFLARE_ACCOUNT_ID`।

```powershell
uv sync --locked --group build --group test
uv run enan-ultra
```

`DEFAULT_AI_MODEL=auto` হলে নির্ধারিত provider order-এর প্রথম উপযুক্ত model ব্যবহৃত হয়। `/models` তালিকা দেখায়, admin `/model provider::model-id` দিয়ে বদলাতে পারেন। `FALLBACK_MODELS` অতিরিক্ত model-level fallback নিয়ন্ত্রণ করে। `AI_FREE_ONLY=true` exact `provider::model-id` allowlist ছাড়া cloud model বন্ধ রাখে; paid fallback-এর জন্য explicit `AI_ALLOW_PAID_FALLBACK=true` ও positive `AI_MAX_ESTIMATED_COST_USD` দরকার। `/providerdiag` local circuit/key health দেখায়, `/providerdiag recheck <provider>` admin-কে provider health check করে quarantine reset করতে দেয়। Provider API key কখনো dashboard/API/log-এ দেখানো হয় না। Details: [`docs/AI_PROVIDER_ARCHITECTURE.md`](docs/AI_PROVIDER_ARCHITECTURE.md), [`docs/PROVIDER_SETUP.md`](docs/PROVIDER_SETUP.md), [`docs/FAILOVER_AND_RETRY.md`](docs/FAILOVER_AND_RETRY.md), [`docs/FREE_TIER_AND_COST_CONTROL.md`](docs/FREE_TIER_AND_COST_CONTROL.md)।

## Mission Control dashboard

Dashboard default-এ `127.0.0.1:8787`-এ bind হয়। Azure VM-এ SSH tunnel তৈরি করুন:

```sh
ssh -L 8787:127.0.0.1:8787 enan-ultra@YOUR_VM
```

তারপর নিজের কম্পিউটারে `http://127.0.0.1:8787` খুলে `/etc/telegram-ai/bot.env`-এর `DASHBOARD_AUTH_TOKEN` দিয়ে প্রবেশ করুন। Login ১২ ঘণ্টার signed session দেয়; login attempt rate-limited এবং state-changing request same-origin যাচাই করে। Dashboard user chat বা Personal KB content দেখায় না। Push-to-talk শুধু microphone চালু করে transcription করে; কোনো command execute করে না।

Home Assistant চালু করতে HTTPS `HA_BASE_URL`, `HA_TOKEN`, `HA_ALLOWED_ENTITIES` এবং `HA_ALLOWED_SERVICES` দিন। `http://localhost`-এর local instance ছাড়া HA endpoint-এ HTTPS বাধ্যতামূলক। শুধু allowlisted entity/service ব্যবহার করা যায়; state-changing request-এ প্রতি বার admin approval লাগে।

## Adaptive theme Mini App

`/theme` দিয়ে auto/manual/off theme চালাও; উদাহরণ: `/theme programming`, `/theme auto`, `/theme learning off`। `/app` Mini App খুলবে। App ঐচ্ছিক এবং HTTPS origin, proxy config ও signed Telegram identity validation চায়; setup এবং সীমাবদ্ধতা [Mini App guide](docs/MINI_APP.md)-এ আছে।

`/vm status|logs|restart`-এ service status, sanitized in-memory app logs এবং approval-সহ graceful bot restart আছে। Restart-এর জন্য systemd unit-এ `Restart=always` ব্যবহৃত হয়। VM controls arbitrary shell command চালায় না।

## Code review ও sandbox

Review/patch-এর জন্য project ZIP-এ reply দিয়ে `/patch <পরিবর্তনের বর্ণনা>` লিখুন। Private chat-এর accepted patch encrypted proposal হিসেবে সর্বোচ্চ ৭ দিন রাখা হয়; rootless Docker চালু থাকলে patch test হবে, তারপর admin review/approval লাগে। Bot running source বদলায় না। Test-এর জন্য ZIP-এ reply দিয়ে `/test` লিখুন, approval-এ `/approve ID` দিন। Test disabled থাকে যতক্ষণ না operator rootless Docker configure করে, image build করে এবং `CODE_SANDBOX_ENABLED=true` দেয়:

```sh
docker build -f deploy/code-sandbox.Dockerfile -t enan-ultra-python-sandbox:py3.15.0-pytest9.1.1 .
```

`deploy/telegram-ai.service` system service-এর জন্য rootless daemon-এর `DOCKER_HOST` ও `XDG_RUNTIME_DIR` systemd drop-in-এ সেট করতে হবে। Bot test শুরু করার আগে Docker daemon-এর rootless status যাচাই করে। Container `--network=none`, read-only root filesystem, dropped capabilities, `no-new-privileges`, PID/CPU/RAM/time caps পায়; project read-only bind mount থেকে bounded tmpfs-এ copy হয়। Runtime-এ image pull হয় না। Sandbox image-এ Python, pytest ও `git apply` থাকে; project-এর অতিরিক্ত dependency দরকার হলে operator-কে trusted prebuilt image দিতে হবে। [Docker rootless mode](https://docs.docker.com/engine/security/rootless/)

## Mission Control ও controlled improvement

- `/mission_new <task>` বাংলা, Roman Bangla বা English request-কে সর্বোচ্চ `AGENT_MAX_STEPS` read-only analysis ধাপে ভাগ করে। `/mission_status ID`, `/mission_list`, `/mission_pause ID`, `/mission_resume ID`, `/mission_cancel ID`, `/mission_report ID` এবং `/mission_delete ID` দিয়ে দেখুন/নিয়ন্ত্রণ করুন। Mission শুধু private chat-এ চলে; arbitrary tools, shell, project file inspection, deployment বা purchase নেই।
- Mission request, plan ও result SQLite-এ encrypted থাকে; ৩টি active mission/user এবং `AGENT_MAX_WORKERS` global worker cap আছে। Restart হলে interrupted mission paused হয়; owner-এর explicit `/mission_resume` ছাড়া পুনরায় চলে না। ৩০ দিন পর content মুছে যায়; terminal metadata তার শেষ update-এর ৯০ দিনের মধ্যে পরিষ্কার হয়।
- `/feedback good|bad [category]` কেবল bounded category রাখে। `/learning_status`, `/learning_pause`, `/learning_resume` admin-only; pause feedback intake, automatic fact extraction ও proposal collection বন্ধ করে, user-এর opt-in memory setting বদলায় বা পুরনো memory মুছে দেয় না। কোনো model weight train হয় না।
- Admin `/evolve_pending` ও `/evolve_review ID` দিয়ে stored proposal, exact diff, test report ও hashes দেখে। `/evolve_approve ID PATCH_SHA256` sandbox-stage gate; `/evolve_stage ID PATCH_SHA256` একই source ZIP-এ patch আবার rootless isolated runner-এ test করে। এরপর `/evolve_deploy_approve ID PATCH_SHA256` পৃথক approval record করে। Production deploy/restart এই workflow চালায় না; configured operator release process আলাদা করে চালাতে হবে।
- সর্বোচ্চ ১০টি active proposal থাকে (creator-পিছু ৩টি); proposal ৭ দিন encrypted থাকে, source ZIP থেকে secret-named file বাদ যায়, tests না পাস করলে approval block হয়। Protected security, dependency, configuration, storage, handler, sandbox ও deployment paths automated evolution test থেকে বাদ। Details: [Mission Control](docs/MISSION_CONTROL_ARCHITECTURE.md), [self-evolution](docs/SELF_EVOLUTION_WORKFLOW.md), [Telegram approvals](docs/TELEGRAM_APPROVALS.md).

## Commands

- General: `/start`, `/help`, `/clear` or `/reset` (clear private conversation history and all stored user memory), `/search <web query>` or `/research <web query>`, `/historysearch <term>`, `/translate <language> <text>`, `/live`, `/exit`, `/id`, `/voice`, `/media`, `/style`, `/language`, `/format text|markdown|json`, `/theme [auto|off|reset|learning on|off|theme-id]`, `/app`, `/privacy local|cloud|export`, `/privacy delete confirm`, `/usage`.
- Generation and exports: `/cancel` stops the active provider request and preserves any generated text; `/export md|txt|pdf` exports saved private chat history, and `/export file ID md|txt` exports extracted text from a retained private-chat document index.
- Memory and files: `/memory`, `/remember <fact>`, `/forget [ID|all]`, `/document [files|forget]`, `/files`, `/forget_files`, `/kb` or `/knowledge` with `on|off|save ID|search <query>|list|delete ID|all`.
- Features: `/agents on|off|status`, `/study on|off|status`, `/study card add <question> :: <answer>`, `/study due`, `/study review ID good|again`, `/study cards`, `/study quiz <topic>`, `/summary` (summarize the latest 80 text messages in a group), `/setmode default|sarcastic|anime|professional|regional` (per-chat response style), `/read https://example.com` (summarize a public HTTPS page), `/feedback good|bad [category]`, `/web on|off`, `/image <prompt>` or `/imagine <prompt>`, `/remind 10m <text>`, `/remind 10m daily|weekly <text>`, `/reminders`, `/cancelreminder ID`, `/tasks add|plan|list|done ID|delete ID`.
- Mission Control: `/mission_new <task>`, `/mission_list`, `/mission_status ID`, `/mission_pause ID`, `/mission_resume ID`, `/mission_cancel ID`, `/mission_report ID`, `/mission_delete ID`.

Group text messages are retained in a bounded, per-group SQLite window (up to 100 messages; `/summary` uses the latest 80). `/read` rejects private hosts and redirects, limits downloaded pages to 2 MiB, and sends only extracted text (up to 3,000 characters) to the configured AI router for summarization. `/search` and `/research` retrieve up to three DuckDuckGo snippets and synthesize a Bengali answer through OpenRouter with source URLs. `/historysearch` searches saved conversation text locally.

Explicit profile facts and background-extracted learned knowledge are collected only from private messages when memory is enabled. `/memory on` enables asynchronous extraction; `/memory learn off` stops extraction while keeping stored memory, and `/memory off` disables both. The conversation pair is sent asynchronously to the configured OpenRouter model after a successful text reply; personal facts remain user-scoped, while only explicitly general, non-sensitive knowledge may be shared as untrusted context. Learning is skipped for temporary chats, local-only mode, and image messages. Existing memory preferences do not silently opt users into background extraction. `/forget all`, `/clear`, `/reset`, and account deletion remove the user's learned facts and any general facts they contributed. `/privacy export` includes stored profile and learned facts. Photo messages use the highest-resolution Telegram photo variant and send an image-modality request to the configured vision-capable provider; `VISION_MODEL` selects the OpenRouter model (default `google/gemini-2.5-flash:free`) when OpenRouter is available. Image byte and pixel limits apply.
- Approvals and devices: `/approve ID`, `/deny ID`; admin-only `/device status|service.domain entity_id`, `/vm status|logs|restart`.
- ZIP workflow: reply to a ZIP with `/test`; reply to a ZIP with `/patch <request>`.
- Admin-only: `/admin`, `/providers`, `/clearuser USER_ID`, `/resetuser USER_ID`, `/model`, `/models`, `/status`, `/end USER_ID`, `/endall` (`/end_all` alias), `/block USER_ID [reason]`, and `/unblock USER_ID`. Reply to a support notice to deliver a formatted admin response. Admin user IDs are checked in every command and support-reply route. Admins can also clear a user's memory from the dashboard's Active users section.
- Admin evolution: `/learning_status`, `/learning_pause`, `/learning_resume`, `/evolve_status`, `/evolve_pending`, `/evolve_review ID`, `/evolve_test ID`, `/evolve_approve ID PATCH_SHA256`, `/evolve_stage ID PATCH_SHA256`, `/evolve_deploy_approve ID PATCH_SHA256`, `/evolve_cancel ID PATCH_SHA256`.

## Privacy, security, backups

`/privacy` bot chat-এ active data flow-গুলো জানায়। Cloud mode-এ request configured provider-এ যেতে পারে; web search, remote file fallback, transcription ও image generation-এর জন্য OpenRouter লাগে। `/imagine` OpenRouter-এর image models API ব্যবহার করে; `IMAGE_MODEL` অবশ্যই OpenRouter-এর বর্তমান image-model catalog-এর model ID হতে হবে, না হলে available image model fallback হবে। `/voice on` configured `TTS_MODEL` থাকলে OpenRouter text-to-speech আগে চেষ্টা করে, ব্যর্থ হলে gTTS ব্যবহার করে; gTTS text Google-এর external TTS service-এ পাঠায়। Local-only mode-এ text/image শুধু configured Ollama-তে যায়; remote TTS, voice transcription, web search ও unsupported-file fallback বন্ধ থাকে। Raw upload সাধারণত memory-তে parse হয়; OCR/ffmpeg-এর অস্থায়ী files কাজ শেষে মুছে যায়। Image byte limit-এর পাশাপাশি `MAX_IMAGE_PIXELS` decompression-bomb ঝুঁকি নিয়ন্ত্রণ করে; scanned PDF OCR-এ rendered page 2048 pixel-এ সীমিত থাকে। Remote provider file request শেষে delete হয় এবং ব্যর্থ হলে retry queue-তে রাখা হয়।

SQLite WAL database-এ versioned migrations, message history, reminder, task, usage, approvals ও audit state থাকে। Personal KB এবং scheduled/manual database backup `APP_DATA_KEY`-derived Fernet key দিয়ে encrypt হয়। Service database-এর পাশের `backups` directory-তে প্রতি ২৪ ঘণ্টায় encrypted snapshot রাখে এবং সর্বশেষ ৭টি রেখে পুরোনো generated snapshot সরায়। Backup restore করতে **একই `APP_DATA_KEY`** লাগবে; key হারালে backup/KB decrypt করা যাবে না। `.env`/systemd env file, Telegram/provider keys, Home Assistant token এবং dashboard secret backup বা logs/API response-এ প্রকাশ করবেন না।

Encrypted database backup ও restore (installer-এর `/etc/telegram-ai/bot.env` থেকে key পড়তে root privilege লাগে):

```sh
sudo env DATABASE_PATH=/var/lib/telegram-ai/assistant.sqlite3 \
  /opt/enan-ultra/.venv/bin/python /opt/enan-ultra/scripts/database_backup.py backup \
  /var/lib/telegram-ai/backups/assistant.sqlite3.enc

sudo systemctl stop telegram-ai
sudo env DATABASE_PATH=/var/lib/telegram-ai/assistant.sqlite3 \
  /opt/enan-ultra/.venv/bin/python /opt/enan-ultra/scripts/database_backup.py restore \
  /var/lib/telegram-ai/backups/assistant.sqlite3.enc --to /var/lib/telegram-ai/assistant.sqlite3 --replace
sudo systemctl start telegram-ai
```

Restore command পুরনো database-কে `.pre-restore-<UTC timestamp>` নামে রেখে backup-টি atomic replace করে, আর restore-এর আগে service বন্ধ আছে কি না যাচাই করে। পুরোনো plaintext backup restore করতে আলাদা করে `--allow-unencrypted` দিতে হয়। Backup-এর off-host copy ও key আলাদা private storage-এ রাখুন।

File content, retrieved passages, usernames ও agent review-কে untrusted data ধরা হয়; tool permission prompt text-এ নির্ভর করে না। Archives path traversal, symlink, member count ও expanded-size checks পায়। Uploaded source/macro/executable স্বাভাবিক file-reading-এ কখনো run হয় না।

## Ubuntu/systemd deployment

Checkout Azure Ubuntu VM-এ নিয়ে চালান:

```sh
sudo bash deploy/install_ubuntu.sh
```

Installer default-এ Python 3.11 ইনস্টল করে (সমর্থিত range 3.11–3.15); অন্য supported version বেছে নিতে `PYTHON_VERSION=3.14 sudo -E bash deploy/install_ubuntu.sh` ব্যবহার করুন। Installer locked dependencies, `ffmpeg`, Poppler, Bengali/English Tesseract, Noto Bengali font, non-root service account ও hardened systemd unit সেট করে। `/etc/telegram-ai/bot.env` root-owned mode `0600` হয়; প্রথমবার secrets generate করে, পরেরবার সেগুলো অক্ষত রাখে এবং পুরনো env file-এ নতুন config keys যোগ করে। Credentials বসিয়ে installer আবার চালান।

```sh
sudoedit /etc/telegram-ai/bot.env
sudo bash deploy/install_ubuntu.sh
sudo systemctl status telegram-ai
sudo journalctl -u telegram-ai -f
```

## Dependency ও verification

`pyproject.toml` direct dependency pins, `uv.lock` transitive lock-এর source of truth। `requirements.txt` lock থেকে export করুন:

```sh
uv lock
uv export --locked --format requirements.txt --no-dev --group build --output-file requirements.txt
uv run --locked --group test pytest
```

CI-তে 3.11–3.15 Python test matrix-এর সঙ্গে correctness lint, Python compile, Mini App JavaScript syntax, wheel build এবং packaged web/theme asset check চলে। Tests provider fallback, local-only routing, encrypted KB isolation, approval authorization/expiry, per-message one-shot tool action, usage caps, image pixel limit, archive traversal/bounds, deployment-secret generation ও config behavior কভার করে। Real Telegram/provider/HA credential এবং rootless Docker daemon ছাড়া external integration smoke test সম্পন্ন হয় না।

## Architecture

`handlers` command ও Telegram message routing করে; `services/telegram_messages.py` shared formatter, Rich Message transport ও fallback; `services/theme_engine.py` topic detection ও per-user themes; `mini_app.py` optional signed Mini App API; `webapp/static` Mini App UI ও `webapp/themes` theme registry; `services/document_export.py` private Markdown/TXT/PDF export; `services/providers.py` provider contract/router; `services/agents.py` bounded specialist review; `files/registry.py` adapter registry; `actions.py` validation ও action approval; `storage/database.py` persistence/migration; `dashboard.py` private admin API/UI; `runtime.py` lifecycle ও cleanup। বিস্তারিত [features](docs/FEATURES.md), [architecture](docs/ARCHITECTURE.md), [Mini App](docs/MINI_APP.md), [Rich Messages](docs/TELEGRAM_RICH_MESSAGES.md), [admin guide](docs/ADMIN_GUIDE.md), [configuration](docs/CONFIGURATION.md), [Azure deployment](docs/DEPLOYMENT_AZURE_VM.md) ও [troubleshooting](docs/TROUBLESHOOTING.md) দেখুন।
