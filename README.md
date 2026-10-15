# Enan Ultra Telegram AI Assistant

Enan Ultra একটি modular, async Telegram AI assistant। এটি configured AI provider ব্যবহার করে; “GPT-6 Astra” কেবল conversational style profile, বাস্তবে চালু model-এর পরিচয় নয়। Bot নিজেকে AI বলে পরিচয় দেয়, মানুষ সেজে কথা বলে না।

## কী আছে

- বাংলা default, multilingual reply, conversation context, Study Mode, user-controlled voice/media preferences, opt-in memory এবং cost/rate limits।
- OpenRouter, Groq, Gemini, Cloudflare Workers AI ও Hugging Face Inference API providers; cloud failover ক্রম OpenRouter → Groq → Gemini → Cloudflare → Hugging Face। Cloud provider-এ comma-separated API keys round-robin শুরু করে, 401/402/429 হলে পরের key চেষ্টা করে এবং সব key ব্যর্থ হলে পরের configured provider-এ যায়। Local-only privacy mode-এ cloud fallback হয় না; সেই mode কেবল configured local Ollama ব্যবহার করে।
- ছবি, screenshot, supported document/audio/video বিশ্লেষণ; file adapters, OCR, QR/barcode, source locators, citation-সহ web search, image generation, transcription ও voice reply। Unsupported format-কে সফলভাবে পড়েছে বলে দাবি করে না।
- Complex research/code/security প্রশ্নে budget-limited specialist review। Specialist agent পরামর্শ দেয়; Telegram/HA/VM action execute করে না। Feedback থেকে repeated issue category-র manual improvement suggestion আসে; prompt বা model নিজে বদলায় না, model train হয় না।
- Encrypted, user-isolated Personal Knowledge Base; user `/kb save` দিয়ে সম্মতি দিলে তবেই স্থায়ী হয়। Per-user document/size cap থাকে; temporary file index নির্ধারিত সময়ে মুছে যায়।
- Loopback-এ private Mission Control dashboard, provider/usage/job/approval/audit view, sanitized in-memory app logs, allowlisted Home Assistant এবং confirmed bot-service restart। Arbitrary shell, reboot বা package update endpoint নেই।
- Admin dashboard-এ disk/RAM availability ও health snapshot থাকে; Linux-এ load average দেখায়, Windows-এ unsupported memory/load values unavailable হিসেবে থাকে। Disk খালি 1 GiB-এর নিচে বা RAM ব্যবহার 95% ছাড়ালে admin-কে ছয় ঘণ্টায় সর্বোচ্চ একবার alert পাঠায়।
- Project ZIP থেকে source review ও downloadable patch। Test শুধু user confirmation-এর পরে rootless, network-বিহীন, disposable container-এ চলে; patch live code-এ apply হয় না।
- `/privacy export` user-owned stored data JSON-এ দেয়; `/privacy delete confirm` account-এর user-linked data মুছে দেয়। Private chat-এর history/docs export/delete হয়; shared group history অন্য সদস্যদের সুরক্ষার জন্য account action-এ ছোঁয়া হয় না।
- Admin `/block USER_ID [কারণ]` ও `/unblock USER_ID` দিয়ে bot-local moderation করতে পারেন। `/remind 10m daily বার্তা` বা `weekly` দিয়ে recurring reminder তৈরি করা যায়।
- Per-user দৈনিক request limit-এর পাশাপাশি `USER_MONTHLY_REQUEST_LIMIT` ও `USER_MONTHLY_COST_LIMIT_USD` দিয়ে account-level monthly budget সীমা নির্ধারণ করা যায়; `/usage` daily ও monthly consumption দেখায়।
- `/study card add প্রশ্ন :: উত্তর`, `/study due`, `/study review ID good|again`-এ ব্যক্তিগত flashcard ও সহজ spaced-review interval আছে; card শুধু private chat-এ ব্যবহৃত হয়। `/study quiz বিষয়` AI-generated MCQ-কে Telegram-এর native interactive Quiz Poll হিসেবে পাঠায়।
- `/temporary on` private chat-এর পরবর্তী AI কথোপকথন history, memory, document বা personal-KB retrieval ছাড়া চালায়; `/temporary off` স্বাভাবিক persistence ফেরায়। সাময়িক uploaded document-এর বিদ্যমান retention limit আলাদা ভাবে প্রযোজ্য।
- `/persona default|expert|creative|research|tutor|professional|debate` ব্যক্তিগত উত্তর-ধরন বদলায়; `/persona custom নির্দেশনা` সর্বোচ্চ 500 অক্ষরের user preference সংরক্ষণ করে এবং system/safety rules-এর অধীন থাকে।
- `/timezone Asia/Dhaka` IANA timezone যাচাই করে সংরক্ষণ করে। `/remind YYYY-MM-DD HH:MM বার্তা` ঐ timezone-এর local time-এ একবারের reminder সেট করে; `/reminders` local time-এ দেখায়, `/reminders pause ID|resume ID` দিয়ে pause/resume এবং `/snooze ID 10m` দিয়ে pending reminder পিছিয়ে দেওয়া যায়। Delivery atomic claim ও bounded exponential retry ব্যবহার করে; relative ও daily/weekly recurrence-ও সমর্থিত।
- `/tasks add priority=high due=2026-12-31 লেখা` task-এ priority ও optional ISO-date deadline যোগ করে; list urgent/high/normal/low ও deadline অনুযায়ী সাজানো।
- `/study quiz বিষয়` একটি AI-generated multiple-choice প্রশ্নকে Telegram native interactive Quiz Poll-এ পাঠায়; `/translate bn|en|ar|hi|ur|es|fr|de|ja|zh text` নির্দিষ্ট ভাষায় অনুবাদ করে। দুটোই user-এর privacy mode ও usage limits অনুসরণ করে।
- `/format text|markdown|json` chat উত্তরের কাঙ্ক্ষিত কাঠামো সেট করে; JSON mode model-এর prompt-ভিত্তিক নির্দেশনা, কঠোর JSON schema validator নয়।

## Local setup

Python 3.11–3.15 এবং `uv` ব্যবহার করুন (Python 3.11 সর্বনিম্ন সমর্থিত সংস্করণ)।

```powershell
python scripts/init_env.py
```

`.env`-এ Telegram token, numeric admin chat/user ID এবং অন্তত একটি supported provider configure করুন। Provider key-র জন্য `OPENROUTER_API_KEYS`, `GROQ_API_KEYS`, `GEMINI_API_KEYS`, `CLOUDFLARE_API_KEYS`, `HUGGINGFACE_API_KEYS` ব্যবহার করা যায়; plural variable ফাঁকা থাকলে সংশ্লিষ্ট singular `*_API_KEY` fallback হিসেবে ব্যবহৃত হয়। Groq, Gemini, Cloudflare ও Hugging Face-এর জন্য সংশ্লিষ্ট `*_MODEL` configure করুন; Cloudflare Workers AI-এর জন্য `CLOUDFLARE_ACCOUNT_ID` লাগবে। Hugging Face-এর OpenAI-compatible endpoint defaults to `https://router.huggingface.co/v1`; সেটি `HUGGINGFACE_BASE_URL` দিয়ে বদলানো যায়। প্রতিটি অনুরোধে provider-এর key rotation শুরু হয় পরের round-robin key থেকে; 401/402/429-এ ওই provider-এর অন্য key-গুলো retry হয়, সবগুলো ব্যর্থ হলে নির্দিষ্ট ক্রমের পরের configured provider চেষ্টা হয়। পুরনো OpenAI, Anthropic, Together AI ও SambaNova credentials আর cloud routing-এ ব্যবহৃত হয় না। Local-only privacy mode-এর জন্য আলাদাভাবে local Ollama configure করা যায়। `APP_DATA_KEY` ও `DASHBOARD_AUTH_TOKEN`-কে `init_env.py` cryptographically secure random value দিয়ে তৈরি করে। `.env` Git-এ tracked নয়; Linux-এ mode `0600`।

```powershell
uv sync --locked --group build --group test
uv run enan-ultra
```

`DEFAULT_AI_MODEL=auto` হলে নির্দিষ্ট failover ক্রমের প্রথম উপযুক্ত model ব্যবহৃত হয়। `/models` তালিকা দেখায়, admin `/model provider::model-id` দিয়ে বদলাতে পারেন। `FALLBACK_MODELS` অতিরিক্ত model-level fallback নিয়ন্ত্রণ করে। Provider API key কখনো dashboard/API/log-এ দেখানো হয় না।

## Mission Control dashboard

Dashboard default-এ `127.0.0.1:8787`-এ bind হয়। Azure VM-এ SSH tunnel তৈরি করুন:

```sh
ssh -L 8787:127.0.0.1:8787 enan-ultra@YOUR_VM
```

তারপর নিজের কম্পিউটারে `http://127.0.0.1:8787` খুলে `/etc/telegram-ai/bot.env`-এর `DASHBOARD_AUTH_TOKEN` দিয়ে প্রবেশ করুন। Login ১২ ঘণ্টার signed session দেয়; login attempt rate-limited এবং state-changing request same-origin যাচাই করে। Dashboard user chat বা Personal KB content দেখায় না। Push-to-talk শুধু microphone চালু করে transcription করে; কোনো command execute করে না।

Home Assistant চালু করতে HTTPS `HA_BASE_URL`, `HA_TOKEN`, `HA_ALLOWED_ENTITIES` এবং `HA_ALLOWED_SERVICES` দিন। `http://localhost`-এর local instance ছাড়া HA endpoint-এ HTTPS বাধ্যতামূলক। শুধু allowlisted entity/service ব্যবহার করা যায়; state-changing request-এ প্রতি বার admin approval লাগে।

`/vm status|logs|restart`-এ service status, sanitized in-memory app logs এবং approval-সহ graceful bot restart আছে। Restart-এর জন্য systemd unit-এ `Restart=always` ব্যবহৃত হয়। VM controls arbitrary shell command চালায় না।

## Code review ও sandbox

Review/patch-এর জন্য project ZIP-এ reply দিয়ে `/patch <পরিবর্তনের বর্ণনা>` লিখুন। Patch downloadable `.diff` হয়; bot project file পরিবর্তন করে না। Test-এর জন্য ZIP-এ reply দিয়ে `/test` লিখুন, approval-এ `/approve ID` দিন। Test disabled থাকে যতক্ষণ না operator rootless Docker configure করে, image build করে এবং `CODE_SANDBOX_ENABLED=true` দেয়:

```sh
docker build -f deploy/code-sandbox.Dockerfile -t enan-ultra-python-sandbox:py3.15.0-pytest9.1.1 .
```

`deploy/telegram-ai.service` system service-এর জন্য rootless daemon-এর `DOCKER_HOST` ও `XDG_RUNTIME_DIR` systemd drop-in-এ সেট করতে হবে। Bot test শুরু করার আগে Docker daemon-এর rootless status যাচাই করে। Container `--network=none`, read-only root filesystem, dropped capabilities, `no-new-privileges`, PID/CPU/RAM/time caps পায়; project read-only bind mount থেকে bounded tmpfs-এ copy হয়। Runtime-এ image pull হয় না। Sandbox image-এ শুধু `pytest` ও Python আছে; project-এর অতিরিক্ত dependency দরকার হলে operator-কে আলাদা, trusted prebuilt image দিতে হবে। [Docker rootless mode](https://docs.docker.com/engine/security/rootless/)

## Commands

- General: `/start`, `/help`, `/clear` or `/reset` (clear private conversation history and explicit long-term memories), `/search <term>`, `/translate <language> <text>`, `/live`, `/exit`, `/id`, `/voice`, `/media`, `/style`, `/language`, `/format text|markdown|json`, `/privacy local|cloud|export`, `/privacy delete confirm`, `/usage`.
- Memory and files: `/memory`, `/remember <fact>`, `/forget [ID|all]`, `/files`, `/forget_files`, `/kb on|off|save ID|search <query>|delete ID|all`.
- Features: `/agents on|off|status`, `/study on|off|status`, `/study card add <question> :: <answer>`, `/study due`, `/study review ID good|again`, `/study cards`, `/study quiz <topic>`, `/feedback good|bad [category]`, `/web on|off`, `/imagine <prompt>`, `/remind 10m <text>`, `/remind 10m daily|weekly <text>`, `/reminders`, `/cancelreminder ID`, `/tasks add|list|done ID|delete ID`.
- Approvals and devices: `/approve ID`, `/deny ID`; admin-only `/device status|service.domain entity_id`, `/vm status|logs|restart`.
- ZIP workflow: reply to a ZIP with `/test`; reply to a ZIP with `/patch <request>`.
- Admin-only: `/clearuser USER_ID` or `/resetuser USER_ID` clears that user's private chat history and long-term memory. `/model`, `/models`, `/status`, `/end USER_ID`, `/endall` (`/end_all` alias), `/block USER_ID [reason]`, and `/unblock USER_ID` are also available to admins. User IDs are checked for authorization by the handlers. Admins can also clear a user's memory from the dashboard's Active users section.

## Privacy, security, backups

`/privacy` bot chat-এ active data flow-গুলো জানায়। Cloud mode-এ request configured provider-এ যেতে পারে; web search, remote file fallback, transcription ও image generation-এর জন্য OpenRouter লাগে। Local-only mode-এ text/image শুধু configured Ollama-তে যায়; remote TTS, voice transcription, web search ও unsupported-file fallback বন্ধ থাকে। Raw upload সাধারণত memory-তে parse হয়; OCR/ffmpeg-এর অস্থায়ী files কাজ শেষে মুছে যায়। Image byte limit-এর পাশাপাশি `MAX_IMAGE_PIXELS` decompression-bomb ঝুঁকি নিয়ন্ত্রণ করে; scanned PDF OCR-এ rendered page 2048 pixel-এ সীমিত থাকে। Remote provider file request শেষে delete হয় এবং ব্যর্থ হলে retry queue-তে রাখা হয়।

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

Installer default-এ Python 3.11 ইনস্টল করে (সমর্থিত range 3.11–3.15); অন্য supported version বেছে নিতে `PYTHON_VERSION=3.14 sudo -E bash deploy/install_ubuntu.sh` ব্যবহার করুন। Installer locked dependencies, `ffmpeg`, Poppler, Bengali/English Tesseract, non-root service account ও hardened systemd unit সেট করে। `/etc/telegram-ai/bot.env` root-owned mode `0600` হয়; প্রথমবার secrets generate করে, পরেরবার সেগুলো অক্ষত রাখে এবং পুরনো env file-এ নতুন config keys যোগ করে। Credentials বসিয়ে installer আবার চালান।

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

Tests provider fallback, local-only routing, encrypted KB isolation, approval authorization/expiry, per-message one-shot tool action, usage caps, image pixel limit, archive traversal/bounds, deployment-secret generation ও config behavior কভার করে। Real Telegram/provider/HA credential এবং rootless Docker daemon ছাড়া external integration smoke test সম্পন্ন হয় না।

## Architecture

`handlers` command ও Telegram message routing করে; `services/providers.py` provider contract/router; `services/agents.py` bounded specialist review; `files/registry.py` adapter registry; `actions.py` validation ও action approval; `storage/database.py` persistence/migration; `dashboard.py` private admin API/UI; `runtime.py` lifecycle ও cleanup। নতুন format-এর জন্য `FileAdapter`, provider-এর জন্য `ProviderAdapter`, Telegram action-এর জন্য ActionRegistry contract বাড়ানো যায়।
