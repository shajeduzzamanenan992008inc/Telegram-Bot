# Enan Ultra Telegram AI Assistant

Enan Ultra একটি modular, async Telegram AI assistant। এটি configured AI provider ব্যবহার করে; “GPT-6 Astra” কেবল conversational style profile, বাস্তবে চালু model-এর পরিচয় নয়। Bot নিজেকে AI বলে পরিচয় দেয়, মানুষ সেজে কথা বলে না।

## কী আছে

- বাংলা default, multilingual reply, conversation context, Study Mode, user-controlled voice/media preferences, opt-in memory এবং cost/rate limits।
- OpenRouter, OpenAI, Anthropic, Gemini ও Ollama adapters; provider order, model ID এবং fallback `.env` থেকে। Local-only privacy mode-এ cloud fallback হয় না।
- ছবি, screenshot, supported document/audio/video বিশ্লেষণ; file adapters, OCR, QR/barcode, source locators, citation-সহ web search, image generation, transcription ও voice reply। Unsupported format-কে সফলভাবে পড়েছে বলে দাবি করে না।
- Complex research/code/security প্রশ্নে budget-limited specialist review। Specialist agent পরামর্শ দেয়; Telegram/HA/VM action execute করে না। Feedback থেকে repeated issue category-র manual improvement suggestion আসে; prompt বা model নিজে বদলায় না, model train হয় না।
- Encrypted, user-isolated Personal Knowledge Base; user `/kb save` দিয়ে সম্মতি দিলে তবেই স্থায়ী হয়। Per-user document/size cap থাকে; temporary file index নির্ধারিত সময়ে মুছে যায়।
- Loopback-এ private Mission Control dashboard, provider/usage/job/approval/audit view, sanitized in-memory app logs, allowlisted Home Assistant এবং confirmed bot-service restart। Arbitrary shell, reboot বা package update endpoint নেই।
- Project ZIP থেকে source review ও downloadable patch। Test শুধু user confirmation-এর পরে rootless, network-বিহীন, disposable container-এ চলে; patch live code-এ apply হয় না।

## Local setup

Python 3.15.0 ও `uv` ব্যবহার করুন।

```powershell
python scripts/init_env.py
```

`.env`-এ Telegram token, numeric admin chat/user ID এবং অন্তত একটি provider configure করুন। Provider উদাহরণ: `OPENROUTER_API_KEY`; অথবা `OPENAI_API_KEY`+`OPENAI_MODEL`, `ANTHROPIC_API_KEY`+`ANTHROPIC_MODEL`, `GEMINI_API_KEY`+`GEMINI_MODEL`, `OLLAMA_BASE_URL`+`OLLAMA_MODEL`। `APP_DATA_KEY` ও `DASHBOARD_AUTH_TOKEN`-ийг `init_env.py` cryptographically secure random value দিয়ে তৈরি করে। `.env` Git-এ tracked নয়; Linux-এ mode `0600`।

```powershell
uv sync --locked --group build --group test
uv run enan-ultra
```

`DEFAULT_AI_MODEL=auto` হলে configured provider order-এর প্রথম উপযুক্ত model ব্যবহৃত হয়। `/models` তালিকা দেখায়, admin `/model provider::model-id` দিয়ে বদলাতে পারেন। `AI_PROVIDER_ORDER` ও `FALLBACK_MODELS` fallback নিয়ন্ত্রণ করে। Provider API key কখনো dashboard/API/log-এ দেখানো হয় না।

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

## প্রধান commands

- সাধারণ: `/start`, `/help`, `/reset`, `/live`, `/exit`, `/id`, `/voice`, `/media`, `/style`, `/language`, `/privacy local|cloud`, `/usage`।
- Memory/files: `/memory`, `/remember`, `/forget`, `/files`, `/forget_files`, `/kb on|off|save ID|search|delete ID|all`।
- Features: `/agents on|off|status`, `/study on|off|status`, `/feedback good|bad [category]`, `/web on|off`, `/imagine prompt`, `/remind`, `/reminders`, `/cancelreminder ID`।
- Approval/device: `/approve ID`, `/deny ID`; admin-only `/device status|service.domain entity_id`, `/vm status|logs|restart`।
- ZIP workflow: ZIP-এ reply দিয়ে `/test`; ZIP-এ reply দিয়ে `/patch request`।
- Admin-only: `/model`, `/models`, `/status`, `/end USER_ID`, `/endall` (`/end_all` alias)। Numeric Telegram user ID handler code-এ permission check পায়।

## Privacy, security, backups

`/privacy` bot chat-এ active data flow-গুলো জানায়। Cloud mode-এ request configured provider-এ যেতে পারে; web search, remote file fallback, transcription ও image generation-এর জন্য OpenRouter লাগে। Local-only mode-এ text/image শুধু configured Ollama-তে যায়; remote TTS, voice transcription, web search ও unsupported-file fallback বন্ধ থাকে। Raw upload সাধারণত memory-তে parse হয়; OCR/ffmpeg-এর অস্থায়ী files কাজ শেষে মুছে যায়। Image byte limit-এর পাশাপাশি `MAX_IMAGE_PIXELS` decompression-bomb ঝুঁকি নিয়ন্ত্রণ করে; scanned PDF OCR-এ rendered page 2048 pixel-এ সীমিত থাকে। Remote provider file request শেষে delete হয় এবং ব্যর্থ হলে retry queue-তে রাখা হয়।

SQLite WAL database-এ versioned migrations, message history, reminder, usage, approvals ও audit state থাকে। Personal KB filename/source/text `APP_DATA_KEY`-derived Fernet key দিয়ে encrypt হয়। Database backup-এর সঙ্গে **একই `APP_DATA_KEY` নিরাপদে** backup রাখুন; key হারালে KB আর decrypt করা যাবে না। `.env`/systemd env file, Telegram/provider keys, Home Assistant token এবং dashboard secret backup বা logs/API response-এ প্রকাশ করবেন না।

Encrypted Personal KB backup ও restore:

```sh
sudo -u enan-ultra env DATABASE_PATH=/var/lib/telegram-ai/assistant.sqlite3 \
  /opt/enan-ultra/.venv/bin/python /opt/enan-ultra/scripts/database_backup.py backup \
  /var/lib/telegram-ai/backups/assistant.sqlite3

sudo systemctl stop telegram-ai
sudo -u enan-ultra env DATABASE_PATH=/var/lib/telegram-ai/assistant.sqlite3 \
  /opt/enan-ultra/.venv/bin/python /opt/enan-ultra/scripts/database_backup.py restore \
  /var/lib/telegram-ai/backups/assistant.sqlite3 --to /var/lib/telegram-ai/assistant.sqlite3 --replace
sudo systemctl start telegram-ai
```

Restore command পুরনো database-কে `.pre-restore-<UTC timestamp>` নামে রেখে backup-টি atomic replace করে, আর restore-এর আগে service বন্ধ আছে কি না যাচাই করে। Backup ও key আলাদা private storage-এ রাখুন।

File content, retrieved passages, usernames ও agent review-কে untrusted data ধরা হয়; tool permission prompt text-এ নির্ভর করে না। Archives path traversal, symlink, member count ও expanded-size checks পায়। Uploaded source/macro/executable স্বাভাবিক file-reading-এ কখনো run হয় না।

## Ubuntu/systemd deployment

Checkout Azure Ubuntu VM-এ নিয়ে চালান:

```sh
sudo bash deploy/install_ubuntu.sh
```

Installer Python 3.15.0, locked dependencies, `ffmpeg`, Poppler, Bengali/English Tesseract, non-root service account ও hardened systemd unit সেট করে। `/etc/telegram-ai/bot.env` root-owned mode `0600` হয়; প্রথমবার secrets generate করে, পরেরবার সেগুলো অক্ষত রাখে এবং পুরনো env file-এ নতুন config keys যোগ করে। Credentials বসিয়ে installer আবার চালান।

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
