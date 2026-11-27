# Enan Ultra feature catalogue

এই নথিতে repository-তে বর্তমানে থাকা user-facing ও operational feature, তাদের command, কার্যকর সীমা এবং এখনো implement না করা capability আলাদা করে লেখা হয়েছে। “Implemented” মানে feature-এর code path আছে; provider/model, Telegram Bot API version, environment configuration বা user permission না থাকলে সেটি ব্যবহারযোগ্য নাও হতে পারে। কোনো model বা provider-এর capability নিশ্চিত না হলে bot সেটি আছে বলে দাবি করে না।

## 1. Chat, response ও Telegram UX — implemented

- বাংলা default এবং Bangla/English-সহ multilingual chat; system instruction Bengali script, Roman Bangla, mixed-language text, informal typos এবং contextual follow-ups বুঝতে configured model-কে নির্দেশ দেয়। Per-user language (`/language bn|en|auto`), tone (`/style natural|concise|detailed`), persona এবং response format বেছে নেওয়া যায়। বাস্তব ভাষা-বোঝার মান নির্বাচিত model/provider-এর উপর নির্ভরশীল; আলাদা spelling-correction API call হয় না।
- Chat-scoped mode: `/setmode default|sarcastic|anime|professional|regional`।
- User persona: `/persona default|expert|creative|research|tutor|professional|debate`; `/persona custom <instructions>`-এ সর্বোচ্চ 500 character-এর পছন্দ যোগ করা যায়। এগুলো system, safety ও privacy rule-এর অধীন।
- `/format text|markdown|json` model-কে response shape নির্দেশ করে; JSON mode prompt instruction, strict schema validation নয়।
- `HISTORY_MESSAGES`-এর history window ও `MAX_INPUT_CHARS`-এর aggregate prior-history character cap, bounded response size, cooldown, daily/monthly request ও cost limit, usage accounting (`/usage`)। Private history user-scoped; group history group-scoped।
- User-এর AI request চলার সময় `/cancel` বা supported Bot API client-এর Stop control generation থামায়। Private chat-এ Rich Message preview-তে live draft এবং supported API-তে stop control দেওয়া হয়; hidden model reasoning প্রকাশ করা হয় না।
- AI answer এবং admin support reply একই Telegram rich-message formatter ব্যবহার করে। API/client support না থাকলে plain text/draft fallback হয়। Telegram-এর সাধারণ text-এ arbitrary font colour সমর্থিত নয়।
- `/export md|txt|pdf` private chat-এর সংরক্ষিত conversation history export করে; `/export file ID md|txt` retained document index-এর extracted text ও source labels export করে। অন্য chat-এর বা expired document export করা যায় না। Bengali PDF shaping-এর জন্য compatible Bengali font দরকার।

## 2. AI provider ও model routing — implemented/configuration-dependent

- Cloud providers: OpenRouter, Groq, Google AI Studio (Gemini), Cloudflare Workers AI এবং Hugging Face Inference API।
- Per-provider comma-separated API key, round-robin selection; 401-এ rejected credential quarantine ও sibling key retry, 402/429-এ account cooldown এবং পরের eligible provider/model failover।
- Configurable provider order, bounded per-key retries/request attempts, circuit breaker, safe provider diagnostics (`/providers`, `/providerdiag`, `/providerdiag recheck <provider>`) এবং model selection/fallback (`/models`, `/model`)।
- Text, tools, image input and streaming route only when the provider catalog or explicit `provider::model-id` capability setting supports the requested feature. Audio/video/document handling remains dependent on the separate extraction and model paths.
- Optional exact-model free-only allowlist and cost-gated paid fallback; provider free access and price can change independently of bot configuration.
- Optional local Ollama route। `/privacy local`-এ cloud fallback নিষিদ্ধ; local provider configure করা না থাকলে AI request ব্যর্থ হবে।
- Provider health/status, timeout handling, usage/cost accounting, configured limit enforcement এবং admin status/usage dashboard।
- Provider-ভিত্তিক modality/model availability, credentials, account quota, free-tier limit ও region নির্ধারণ করে কোন feature বাস্তবে চলবে। “Free” provider/model-এর quota নিশ্চয়তা bot দেয় না।

## 3. Image, voice, file ও sticker — implemented/configuration-dependent

- Photo/image analysis: Telegram photo-র highest-resolution variant download করে configured vision-capable model-এ image modality-সহ পাঠায়; `VISION_MODEL` model নির্বাচন করে। Image byte ও pixel limit প্রয়োগ হয়।
- `/image <prompt>` বা `/imagine <prompt>` image generation করে; OpenRouter image API/model configuration ও available model দরকার।
- Voice input transcription এবং voice reply feature provider/configuration-এর উপর নির্ভরশীল। `/voice on|off` per-user reply preference নিয়ন্ত্রণ করে। Configured OpenRouter TTS আগে চেষ্টা হয়; ব্যর্থ/অনুপলব্ধ হলে gTTS fallback text Google-এর external TTS service-এ পাঠায়। Local-only mode-এ remote TTS/transcription নিষ্ক্রিয়।
- Supported audio/video/document format-এ extraction, OCR এবং প্রাসঙ্গিক source/page/line locator; unsupported format-কে পড়া হয়েছে বলে দেখায় না। Temporary upload/file processing-এ configured size/retention limit প্রযোজ্য।
- Optional ClamAV INSTREAM check Telegram photo, audio/video, document ও project ZIP parsing/provider upload-এর আগে চালানো যায়। Malware detection upload block করে এবং admin chat-এ rate-limited alert পাঠায়; scanner unavailable বা inconclusive হলে enabled mode-এ upload fail-closed হয়। Ubuntu installer ClamAV daemon ও signature updater install করে এবং deployed config-এ scanner enable করে। এটি signature-based scan, endpoint detection suite নয়।
- SSH brute-force event-এর Fail2Ban-to-Telegram alert integration এই bot-এ অন্তর্ভুক্ত নয়; সেটি আলাদা server-side deployment configuration।
- Sticker collector default-off, configured admin-only এবং private-chat-only। `/collect_on` / `/collect_off` toggle করে; individual stickers ও `/import_sticker_set <short_name>`-এর imported pack metadata মূল async SQLite database-এ pending approval হিসেবে যায়। Unique file ID-তে duplicate বাদ পড়ে; unsupported/unknown metadata optional থাকে।
- Sticker catalog admin tools: `/sticker_stats`, `/sticker_search <query>`, `/sticker_review`, `/sticker_categorize ID CATEGORY [tags]`, `/sticker_export`; importer `/cancel_sticker_import` দিয়ে batch boundary-তে থামানো যায়। Category suggestion emoji ও pack metadata-ভিত্তিক এবং uncertain items `general`; emoji sticker-এর প্রকৃত emotion নিশ্চিত করে না। Admin approval ছাড়া sticker inline বা automatic reply-তে পাঠানো হয় না।
- Catalog category, tags, animated/video/regular format, set name/title, approval, usage count এবং last-use cooldown data রাখে; main DB backup path-এ পড়ে। Legacy `stickers_store.db` catalog startup-এ একবার pending review-তে migrate হয়; পুরোনো source file মুছে ফেলা হয় না।
- BotFather-এ inline mode সক্রিয় থাকলে `@BOT_USERNAME <metadata query>` সর্বোচ্চ ২০টি approved sticker match দেয়; blank query approved catalog-এর জনপ্রিয় sticker দেখায়।
- OpenRouter provider file fallback ব্যবহার হলে request শেষে uploaded file delete করার চেষ্টা হয়; cleanup ব্যর্থ হলে retry queue-তে থাকে।

## 4. Study ও group productivity — implemented

- `/study on|off|status`; `/study quiz <topic>` AI-generated MCQ-কে native Telegram Quiz Poll-এ পাঠায় (correct option, explanation, non-anonymous quiz configuration)।
- ব্যক্তিগত flashcards: `/study card add <question> :: <answer>`, `/study cards`, `/study due`, `/study review <ID> good|again`। Review interval সহজ spaced-review schedule; full adaptive learning নয়।
- Group-এর সর্বশেষ bounded text-message window SQLite-এ থাকে; `/summary` সাম্প্রতিক group discussion-এর Bengali summary তৈরি করে। Group summary-তে history group-scoped; account-level clear shared group history মুছে না।
- `/read <https-url>` public webpage fetch করে clean text extract করে configured AI দিয়ে summary বানায়; URL validation, redirect/size/time limit প্রযোজ্য।
- `/search <query>` বা `/research <query>` search snippet-ভিত্তিক live web answer source URL-সহ তৈরি করে; এটি unrestricted autonomous crawler নয়।
- `/translate <language> <text>` সমর্থিত নির্দিষ্ট ভাষায় অনুবাদ করে; usage এবং privacy setting মানে।

## 5. Memory, privacy ও data lifecycle — implemented

- `/memory` দিয়ে memory status/তথ্য দেখা; `/memory on|off` consented long-term memory নিয়ন্ত্রণ করে। `/remember <fact>` explicitly জমা দেওয়া fact সংরক্ষণ করে; `/forget [ID|all]` মুছে দেয়।
- Continuous fact extraction: `/memory on` দিলে successful private text reply-এর পরে application-managed background task OpenRouter-এ user text ও bot response-এর সীমিত অংশ পাঠিয়ে candidate fact JSON-এ extract করে। Extractor-কে bot response-কে fact source হিসেবে না ধরতে বলা হয়।
- `/memory learn on|off` automatic extraction আলাদাভাবে চালু/বন্ধ করে; পূর্বের memory preference থাকা users-কে স্বয়ংক্রিয়ভাবে background extraction-এ opt-in করানো হয় না। `/memory off` memory এবং learning উভয়ই বন্ধ করে।
- Personal preference/fact/entity কেবল সংশ্লিষ্ট user scope-এ সংরক্ষিত ও context-এ যোগ হয়। কেবল extractor non-personal, non-sensitive, durable ও explicitly general হিসেবে চিহ্নিত করলে global knowledge scope-এ রাখা হয়। Learned content untrusted reference data; instruction হিসেবে মানা হয় না।
- Automatic extraction private text chat-এ সীমিত; temporary chat, local-only privacy, photo/image message এবং memory/learning opt-in না থাকলে চলে না। Extractor failure/invalid JSON হলে chat reply-তে বাধা হয় না।
- `/clear` বা `/reset` private conversation history ও memory মুছে; `/clearuser USER_ID` ও `/resetuser USER_ID` admin command নির্দিষ্ট user-এর private data মুছে। Dashboard-এর Active users-এ admin Clear Memory action আছে।
- `/temporary on|off` পরবর্তী private AI chat-এ history, memory, document ও personal-KB retrieval বন্ধ করে; temporary-তে নতুন conversation history save হয় না।
- `/privacy` data flow জানায়; `/privacy export` user-owned তথ্য JSON-এ দেয়; `/privacy delete confirm` user-linked account data মুছে। Shared group conversation অন্য সদস্যদের সুরক্ষায় account clear/delete-এ মুছে ফেলা হয় না।
- Personal Knowledge Base (`/kb on|off|save|search|list|delete`) encrypted storage, per-user quota এবং opt-in save-সহ। Temporary uploaded-file text bounded retention-এ।
- Database SQLite WAL, versioned migration, encrypted backup/KB support; backup restore করতে একই `APP_DATA_KEY` প্রয়োজন।

## 6. Tools, agents ও automation — implemented/configuration-dependent

- Budget-limited specialist review for selected multi-step research/code/security requests; specialist output suggestion, automatic code change নয়। `/agents on|off|status`।
- Approval-gated tools, allowlisted Home Assistant actions এবং controlled VM service operation (`/device`, `/vm`)। Arbitrary shell/reboot/package update endpoint নেই।
- Reminder: relative time, local timezone (`/timezone Area/City`), one-time/date-time, daily/weekly recurrence, `/reminders`, pause/resume, `/snooze`, `/cancelreminder`; delivery retry bounded।
- Personal task list: `/tasks add|list|done|delete`; priority, optional deadline ও `after=ID[,ID...]` prerequisites। `/tasks plan <goal|chat>` configured provider-এ budget/privacy policy অনুসরণ করে সর্বোচ্চ 10টি dependent personal task তৈরি করে; `chat` কেবল bounded private history ব্যবহার করে এবং temporary chat-এ বন্ধ থাকে। Unfinished prerequisite থাকলে dependent task complete করা যায় না বা prerequisite task delete করা যায় না। কোনো text/caption message-এ reply করে `/tasks add` পাঠালে সেটি task হিসেবে সংরক্ষিত হয়।
- Feedback collection (`/feedback good|bad`) এবং repeated issue category-র manual suggestion; automatic model training/prompt self-modification নয়।
- `/live` ও configured trigger-word-এ admin support handoff; reply mapping, delivery status ও audit।

## 7. Admin dashboard ও operations — implemented/configuration-dependent

- Dashboard loopback interface-এ authenticated admin-only provider, usage, jobs, approvals, audit, active users ও sanitized application status/log view; provider keys/private chat content প্রকাশ করে না।
- Active users থেকে private memory clear করা যায়। Dashboard access token এবং same-origin protection প্রয়োজন; external exposure করার জন্য এটি public web app হিসেবে ডিজাইন করা হয়নি।
- Disk/RAM health snapshots এবং configured threshold/cooldown-এ admin alert; platform অনুযায়ী কিছু metric unavailable হতে পারে।
- Linux systemd deployment/installer, structured logs, maintenance cleanup, encrypted backup retention এবং restart-এর পরে pending provider-file cleanup।
- Project ZIP source review এবং downloadable patch। Test চালানো হয় কেবল user confirmation-এর পরে disposable, rootless, network-isolated container-এ; generated patch live project-এ auto-apply হয় না।

## 7A. Adaptive theme ও Telegram Mini App — আংশিক implemented/configuration-dependent

- `services/theme_engine.py` English, বাংলা keyword/phrase, নির্বাচিত Roman Bangla spelling এবং সাম্প্রতিক bounded context থেকে topic score করে। Theme registry-তে programming, chemistry, physics, mathematics, astronomy, biology, AI/robotics, business, education ও general palette আছে। এটি deterministic keyword/context matcher, typo-tolerant NLU model নয়।
- Confidence কম হলে current theme ধরে রাখে; সাধারণ topic switch-এ hysteresis প্রয়োগ করে, স্পষ্ট switch cue থাকলে দ্রুত বদলায়। এটি rule-based detector, model classification নয়; detector-এর জন্য আলাদা provider request বা খরচ নেই।
- `/theme auto`, `/theme <theme-id>`, `/theme off`, `/theme reset`, `/theme learning on|off` user preference নিয়ন্ত্রণ করে। User-এর appearance, palette, animation, favorites ও theme choice SQLite preference store-এ user ID অনুযায়ী থাকে। Learning বন্ধ করলে favorite feedback নতুন preferred theme বদলায় না।
- Optional Mini App `/app`, `/start` inline button এবং Telegram menu button থেকে খোলে; responsive animated UI-তে theme selection, appearance/motion settings ও authenticated chat streaming আছে। Mini App চালু করতে `MINI_APP_URL` HTTPS origin এবং reverse proxy দরকার।
- Telegram bot নিজে wallpaper বা Telegram client-এর native color controls বদলায় না। Theme metadata assistant/support reply-তে icon ও label যোগ করে; full-window palette/canvas কেবল Mini App-এ প্রযোজ্য।
- This implements an adaptive workspace, not the planned full holographic 3D command center. Topic detection can misclassify ambiguous short text; users can pin a theme or turn auto mode off. Mini App setup details: [Mini App guide](MINI_APP.md).

বিষয়ভিত্তিক registry, Roman Bangla phrase list এবং accessibility customization: [Adaptive Theme Engine](ADAPTIVE_THEME_ENGINE.md), [Topic Detection](TOPIC_DETECTION.md), [Theme Customization](THEME_CUSTOMIZATION.md), [Performance and Accessibility](PERFORMANCE_AND_ACCESSIBILITY.md), [Mini App Setup](MINI_APP_SETUP.md), [Security boundaries](SECURITY.md)।

## 7B. Multimodal ও Knowledge Studio — implemented/configuration-dependent

- Voice/audio, image, video, PDF, Office, EPUB, text/code/config ও bounded archive parsing-এর বর্তমান routing এবং সীমা: [Multimodal Architecture](MULTIMODAL_ARCHITECTURE.md), [Voice Intelligence](VOICE_INTELLIGENCE.md), [Document Processing](DOCUMENT_PROCESSING.md)।
- Explicit consent-এর পর encrypted personal KB-তে document সংরক্ষণ ও passage search: [Knowledge Base](KNOWLEDGE_BASE.md)। এটি vector/embedding index নয়।
- `/search`, `/read`, এবং configured `/web on`-এর live search সীমা: [Web Research](WEB_RESEARCH.md)।
- File retention, Temporary Chat, cloud/local routing এবং provider-side retention boundary: [Privacy and File Retention](PRIVACY_AND_FILE_RETENTION.md)।
- Modality capability, model selection, provider fallback ও cost policy: [Provider Capabilities](PROVIDER_CAPABILITIES.md)।

## 8. Commands quick index

| Area | Commands |
|---|---|
| Chat | `/start`, `/help`, `/id`, `/clear`, `/reset`, `/cancel`, `/export md|txt|pdf` |
| Memory/privacy | `/memory on|off|show`, `/memory learn on|off`, `/remember`, `/forget`, `/temporary on|off`, `/privacy`, `/privacy export`, `/privacy delete confirm` |
| Preferences | `/language`, `/style`, `/format`, `/persona`, `/setmode`, `/theme`, `/app`, `/media`, `/voice`, `/timezone` |
| AI/tools | `/providers`, `/models`, `/model`, `/usage`, `/web`, `/search`, `/read`, `/translate`, `/agents`, `/study`, `/kb` |
| Productivity | `/summary`, `/remind`, `/reminders`, `/cancelreminder`, `/snooze`, `/tasks` |
| Media | `/image`, `/imagine`, `/make_sticker` (reply to a photo); photo analysis by sending a photo |
| Admin | `/clearuser`, `/resetuser`, `/block`, `/unblock`, `/status`, `/live`, `/exit`, `/end`, `/endall`, `/approve`, `/deny`, `/device`, `/vm` |

Command availability depends on authorization, chat type, privacy mode, provider configuration and Telegram Bot API support. `/help` and BotFather command menu show the active bot command descriptions.

## 9. Not implemented / explicit limitations

নিচের বিষয়গুলোকে implemented feature হিসেবে ধরা যাবে না:

- Voice cloning, কোনো নির্দিষ্ট ব্যক্তির কণ্ঠ নকল, অথবা emotion অনুযায়ী voice prosody/voice identity clone। বর্তমান voice reply হলো সাধারণ TTS।
- Bot-এর নিজে থেকে training, fine-tuning, model weights পরিবর্তন, মানুষের মতো স্থায়ী autonomous learning, বা unsupervised model/prompt self-edit। Background extractor শুধু opt-in conversation-এ bounded facts store করে।
- Vector embedding/semantic long-term memory index। Current learned facts SQLite structured key/value record; retrieval সীমিত user/global bounded list, vector search নয়।
- Automatic general knowledge gathering, factual verification, confidence/expiry workflow, knowledge conflict resolution, provenance browsing UI, or global knowledge moderation UI. General records-এ উৎস user ID রাখা হয় এবং সেটি মুছে দিলে সংশ্লিষ্ট shared record-ও মুছে যায়; তবে source ID থাকা claim-টির সত্যতা নিশ্চিত করে না।
- Continuous group conversation learning. Fact extractor group chat-ல் চালানো হয় না।
- Automatic realtime web crawling/scheduled sports, crypto, stock or news monitoring; `/search` user-triggered search snippets-এ সীমিত, `/read` user-supplied public URL-এ।
- Unrestricted web scraping, arbitrary URL fetch, or sources-এর truth guarantee.
- Rich inline-button styling, rich media composition, direct Rich Message document blocks, PDF table of contents এবং automatic service self-healing।
- Arbitrary admin shell, remote machine reboot, package update, বা user ZIP patch auto-apply।
- Provider-নির্দিষ্ট model-এর live availability/quota guarantee; এটি runtime credentials, provider account, model catalog ও network-এর উপর নির্ভর করে।

## 10. Ultra AI / OMEGA NEXUS PRIME feature map

নিচের তালিকাটি প্রস্তাবিত “All-in-One Ultra AI” ধারণাগুলোর বর্তমান codebase-এ অবস্থা। এটি নতুন integration চালু করে না; বাস্তবে থাকা feature আর roadmap-ধারণা আলাদা করে।
এই section-এর 1–20 হলো ছোট feature-map-এর local numbering; stable reference হিসেবে পরের 01–120 registry ID ব্যবহার করতে হবে।

| # | প্রস্তাবিত capability | বর্তমান অবস্থা | বাস্তব scope |
|---:|---|---|---|
| 1 | Smart AI Brain | Implemented, model-dependent | Chat, context, reasoning/উত্তর generation ও কিছু tool route। ফলাফলের গুণমান নির্বাচিত model-এর উপর নির্ভরশীল। |
| 2 | Voice Assistant | আংশিক implemented | Telegram voice input/transcription ও TTS reply configuration-সাপেক্ষ। Hands-free, always-listening voice control নেই। |
| 3 | Multimodal AI | আংশিক implemented | Photo, supported file/audio/video processing এবং image generation আছে; প্রতিটি modality-র জন্য compatible model/tool দরকার। |
| 4 | AI Memory | opt-in-এ আংশিক implemented | Structured SQLite profile fact, explicit `/remember`, এবং opt-in background fact extraction। Vector memory বা model training নয়। |
| 5 | Coding Assistant | আংশিক implemented | Project ZIP review/test/patch proposal workflow আছে; approval ছাড়া patch live code-এ apply হয় না। সাধারণ-purpose IDE/computer agent নয়। |
| 6 | Device Control | সীমিতভাবে implemented | Allowlisted Home Assistant ও controlled VM actions, permission/approval boundary-সহ। Desktop app খোলা বা arbitrary OS command চালানো নেই। |
| 7 | Web Search | Implemented, user-triggered | `/search` সীমিত search result/snippet নিয়ে source-সহ উত্তর বানায়; autonomous crawl/monitor নয়। |
| 8 | Multiple AI Providers | Implemented/configuration-dependent | OpenRouter, Groq, Gemini, Cloudflare Workers AI, Hugging Face ও optional Ollama adapter। OpenAI/Claude provider এই বর্তমান provider তালিকায় নেই। |
| 9 | Automatic API Fallback | Implemented/configuration-dependent | Bounded cross-provider failover, account-aware 402/429 cooldown, invalid-key quarantine, attempt cap ও circuit breaker আছে। `/privacy local`-এ local failure হলে cloud API-তে যাওয়া ইচ্ছাকৃতভাবে নিষিদ্ধ। |
| 10 | Tool Calling | সীমিতভাবে implemented | AI function call কেবল registered tools-এ পৌঁছায়; sensitive action-এর authorization/approval প্রয়োজন। |
| 11 | Study Mode | Implemented | Step-by-step study preference, flashcards, review schedule ও Telegram quiz poll। |
| 12 | File Assistant | Implemented, format/limit-dependent | Supported document/media parse, OCR ও source locator; unsupported format-এ সফলতার ভান করে না। |
| 13 | Multilingual | Implemented, model-dependent | Bengali default, user language preference ও supported language translation। |
| 14 | Telegram Integration | Implemented/partial | Commands, message/media handlers, polls, inline sticker query, private dashboard workflow এবং optional authenticated Mini App। |
| 15 | WhatsApp Integration | Not implemented | Approved WhatsApp Business/API integration, webhook, consent ও deployment আলাদা করে তৈরি করতে হবে। |
| 16 | Automation | Implemented | User reminders, recurring reminders, tasks, timezone ও bounded retry। |
| 17 | Monitoring & Alerts | Implemented/configuration-dependent | Health/resource snapshot, provider diagnostics, rate-limited failover-exhausted alert, audit/log view ও configured resource alert। পূর্ণ SIEM নয়। |
| 18 | Security System | Implemented boundaries | Admin/user authorization, rate/cost limit, audit, privacy modes, approval gate, dashboard auth, URL/file/sandbox restriction। Security নিশ্চয়তা নয়; deployment hardening জরুরি। |
| 19 | Knowledge Base | Implemented | User-isolated encrypted Personal KB এবং opt-in document save/retrieval। |
| 20 | Self-Improvement | সীমিতভাবে implemented | Feedback সংগ্রহ ও manual improvement suggestion; স্বয়ংক্রিয় prompt/model পরিবর্তন বা training নয়। |

### Advanced concept mapping

- **Multi-Agent System — আংশিক আছে:** নির্দিষ্ট জটিল request-এ budget-limited specialist review coordinator-এর জন্য draft দেয়। Specialist স্বাধীনভাবে Telegram action করে না এবং সব request-এ agents চলে না।
- **Autonomous Task Execution — সীমিত আছে:** task list, reminders, bounded agent review ও approval-gated registered tools আছে। General goal planner, unrestricted agent loop, arbitrary recovery/action execution নেই।
- **Knowledge Learning — opt-in, সীমিতভাবে আছে:** `/memory on` এবং automatic learning enabled হলে private text reply-এর পরে bounded fact extraction হয়। Global knowledge candidate-ও user-submitted content; এটি fact-checking, curated encyclopedia বা মূল AI model-এর training নয়।
- **Holographic Command Center — নেই:** বর্তমান dashboard হলো authenticated loopback admin web UI; animated 3D Earth/holographic visualization বা real-time interactive voice-control center নেই।

## 10A. OMEGA NEXUS PRIME — 20 core AI assistant capabilities

নিচের তালিকাটি “All-in-One Ultra AI Assistant” ধারণার core feature blueprint; এটি repository-এ সবসময় production-ready feature নয়, বরং roadmap ও product vision-এর অংশ। বাস্তবে কোনটা implemented/partial/planned সেটি পূর্বের section-এ status-সহ উল্লেখ করা আছে।

1. Smart AI Brain  
   সাধারণ কথোপকথন, reasoning, problem solving, contextual answer generation, এবং task planning।
2. Voice Assistant  
   speech-to-text, text-to-speech, voice commands, hands-free interaction, এবং কণ্ঠস্বর-ভিত্তিক response।
3. Multimodal AI  
   text, image, screenshot, document, audio/video input-এ কাজ; 필요한 হলে image generation/voice generation।
4. AI Memory  
   user profile, preference, prior conversation context, and task memory।
5. Coding Assistant  
   code generation, debugging, project analysis, testing support, refactoring guidance।
6. Device Control  
   allowlisted app/device automation, commands, and smart-home style control with permission guardrails.
7. Web Search  
   live information retrieval, query-driven answer synthesis, source-aware summarization।
8. Multiple AI Providers  
   OpenRouter, Gemini/Google AI Studio, Groq, Cloudflare Workers AI, Hugging Face Inference Providers, and optional local Ollama.
9. Automatic API Fallback  
   bounded retries for transient errors, no sibling-key rotation on account quota/429, and configured cross-provider failover.
10. Tool Calling  
   search, calculator, file ops, database bridge, and approved tool execution with permission checks.
11. Study Mode  
   explanation-based tutoring, quiz generation, flashcards, review loops, and native Telegram quiz polls.
12. File Assistant  
   PDFs, DOCX, code, and media content analysis with summary and extraction support.
13. Multilingual  
   বাংলা, English, and supported language outputs with locale-aware phrasing.
14. Telegram Integration  
   Telegram bot commands, media handling, polling, notifications, and interactive flows.
15. WhatsApp Integration  
   optional business/integration layer for cross-channel assistant workflows.
16. Automation  
   reminders, scheduled tasks, workflows, and recurring agent actions.
17. Monitoring & Alerts  
   service health checks, provider status, limit tracking, and operational notifications.
18. Security System  
   admin auth, user auth, audit logs, safe tool execution, rate limit, and permission boundaries.
19. Knowledge Base  
   project documents, notes, and user-approved knowledge sources for answer grounding.
20. Self-Improvement  
   feedback collection, performance analysis, and safe iterative improvements without unsafe autonomous retraining.

### AI architecture blueprint for OMEGA NEXUS PRIME

- User Interface: Telegram, Voice, Web Dashboard
- AI Core + Memory + Tool Router
- Intent · Planning · Permissions · Context
- Local AI: private/local model, offline-safe mode
- API Fallback: cloud provider failover and retry chain
- Tools & Integrations: web, files, apps, database, notifications
- Security & Governance: approval, audit, privacy, sandboxing

### GPT-6 Astra as primary brain

Bot-এ “GPT-6 Astra” কে primary brain হিসেবে ধরা যেতে পারে; এটি reasoning, coding, problem solving, planning, and multi-step work orchestration-এর জন্য best-fit choice. Telegram integration, persistent memory, local-AI fallback, provider routing, and security monitoring আলাদা application layer-এ implement করতে হবে।

একটি verdadeira ultra assistant-এ model একটাই নয়; বরং architecture হলো: primary reasoning model + local fallback + provider failover + tool router + memory layer + safe automation + user-facing UX. এখানে API, budget, quota, availability, এবং region-based routing-সহ intelligent provider orchestration গুরুত্বপূর্ণ।

### প্রস্তাবিত logical architecture বনাম বর্তমান implementation

```text
User interface
  ├─ Implemented: Telegram text/media/commands/polls/inline sticker query
  ├─ Implemented: loopback admin dashboard
  ├─ Partial: authenticated adaptive theme Mini App and streamed chat (optional HTTPS setup)
  ├─ Partial: voice input/output through configured services
  └─ Not implemented: WhatsApp, always-listening hands-free UI, holographic UI

AI core and context
  ├─ Implemented: provider router, model/provider fallback, bounded specialist review
  ├─ Implemented: conversation context, opt-in structured memory, Personal KB
  └─ Not implemented: vector memory, autonomous self-training, unrestricted planner

Models and integrations
  ├─ Implemented/configurable: OpenRouter, Groq, Gemini, Cloudflare, Hugging Face
  ├─ Optional: local Ollama; local-only mode never falls back to cloud
  └─ Implemented with guardrails: web search, file tools, reminders, HA/VM actions
```

**“GPT-6 Astra” নোট:** এই নামকে এখানে user-এর প্রস্তাবিত brain/model label হিসেবেই ধরা হয়েছে। বর্তমান repository-র verified capability হলো `.env`/runtime-এ configure করা provider ও model; `GPT-6 Astra` নামে model availability, official feature, reasoning/computer-use capability বা OpenAI provider integration এই project documentation থেকে নিশ্চিত নয়। README-র persona profile-ও model identity নয়।

### Out of scope until separately implemented

বর্তমান project-এ নিচের প্রস্তাবগুলো এখনো নেই:

- WhatsApp Business integration ও webhook.
- Hands-free/always-on voice activation এবং ব্যক্তির voice cloning বা emotion-cloned voice.
- Desktop/browser computer-use agent, arbitrary app/command execution.
- Unrestricted autonomous task planner, self-healing action loop বা auto-apply code edits.
- Holographic/3D command center.
- Vector embedding memory, autonomous model training/fine-tuning, self-editing prompt/model.
- Unsupervised live news/finance/sports crawler বা scheduled intelligence service.

## 11. OMEGA NEXUS PRIME — feature registry snapshot

নিচের registry feature-গুলো মূল roadmap-এ core AI assistant capability-র reference হিসেবে সংক্ষিপ্তভাবে রাখা হয়েছে। এগুলো codebase-এর current state থেকে আলাদা।

| ID | Feature | Status | Notes |
|---:|---|---|---|
| 1 | Smart AI Brain | Implemented/partial | Depends on active model and routing |
| 2 | Voice Assistant | Partial | TTS/voice input needs configuration and policy |
| 3 | Multimodal AI | Partial | Photo/file processing and image generation supported under provider compatibility |
| 4 | AI Memory | Partial | Structured fact memory and opt-in extraction available |
| 5 | Coding Assistant | Partial | Project review/patch proposal workflow exists |
| 6 | Device Control | Limited | Requires allowlisting and approval flow |
| 7 | Web Search | Implemented | User-triggered search with summarization |
| 8 | Multiple AI Providers | Implemented/config-dependent | Multi-provider config, ordered routing, diagnostics and failover path |
| 9 | Automatic API Fallback | Implemented/config-dependent | Retry budget, circuit breaker, quota cooldown, invalid-key quarantine and cross-provider fallback |
| 10 | Tool Calling | Partial | Safe tool execution with registry-based access |
| 11 | Study Mode | Implemented | Quiz polls and learning flows |
| 12 | File Assistant | Partial | Supported formats and limits apply |
| 13 | Multilingual | Implemented | Bengali/English supported with user preferences |
| 14 | Telegram Integration | Implemented/partial | Bot, commands, media, alerts, inline UX and optional authenticated Mini App |
| 15 | WhatsApp Integration | Planned | Separate integration layer required |
| 16 | Automation | Partial | Reminders and tasks supported |
| 17 | Monitoring & Alerts | Partial | Runtime health and operational observability |
| 18 | Security System | Implemented | Auth, rate limiting, privacy, sandbox boundary |
| 19 | Knowledge Base | Implemented | User-approved KB and memory store |
| 20 | Self-Improvement | Limited | Feedback loops without autonomous retraining |

## 11. OMEGA NEXUS PRIME — 120-item feature registry

সংযুক্ত Ultra AI blueprint-এর feature-গুলোকে নিচে unique ID, বর্তমান implementation status, suggested phase/priority এবং প্রধান dependency-সহ তালিকাভুক্ত করা হলো। এটি roadmap, implementation commitment নয়। `Implemented` মানে সীমাবদ্ধতাসহ code path আছে; `Partial` মানে related capability আছে কিন্তু পুরো feature নয়; `Planned` মানে এখনও নেই।

**Priority/phase:** P0/V1 foundation; P1/V2 multimodal/workspace; P2/V3 multi-agent automation; P3/V4 controlled evolution; P4/V5 experimental frontier. API/provider feature-এ compatible model, credentials ও account entitlement লাগতে পারে।

### 01–12 · Intelligence core

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 01 | Universal chat brain — configured languages-এ Telegram conversation | Implemented | P0/V1 · Telegram + model |
| 02 | Deep reasoning engine — complex task-এ reliable multi-step reasoning | Partial | P1/V2 · model + evaluation |
| 03 | Intent understanding — request-কে valid route/tool-এ map করা | Partial | P1/V2 · tool schemas + policy |
| 04 | Context awareness — bounded history ও consented context ব্যবহার | Implemented | P0/V1 · SQLite + privacy |
| 05 | Adaptive explanation — user style/study mode অনুযায়ী ব্যাখ্যা | Implemented | P1/V2 · profile settings |
| 06 | Planning intelligence — goal থেকে dependency/checkpoint-সহ plan | Partial | P2/V3 · task graph |
| 07 | Answer verification — source/calculation/test-এ claim যাচাই | Partial | P2/V3 · verifier + evidence |
| 08 | Uncertainty detection — uncertain answer চিহ্নিত করা | Partial | P1/V2 · calibrated model |
| 09 | Contradiction detector — stored facts/answers-এর বিরোধ শনাক্ত | Planned | P3/V4 · provenance memory |
| 10 | Multi-provider brain — configured providers-এ route | Implemented | P0/V1 · provider configuration |
| 11 | Cost-quality router — cost, latency, quality একত্রে বিবেচনা | Partial | P2/V3 · model metrics |
| 12 | Failure recovery brain — bounded retry/fallback ও graceful failure | Implemented | P0/V1 · router + timeout policy |

### 13–22 · Multimodal

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 13 | Text ও rich formatted response | Implemented | P1/V2 · compatible Bot API/client |
| 14 | Voice message speech recognition | Partial | P1/V2 · transcription provider |
| 15 | AI-generated voice response | Partial | P1/V2 · OpenRouter TTS/gTTS |
| 16 | Voice/text output preference switch | Implemented | P1/V2 · user preference |
| 17 | Photo/screenshot/diagram analysis | Implemented | P1/V2 · vision model + limits |
| 18 | PDF/DOCX/spreadsheet/code analysis | Implemented | P1/V2 · format adapters |
| 19 | Supported audio analysis/transcription | Partial | P1/V2 · audio model/decoder |
| 20 | Image generation integration | Implemented | P1/V2 · OpenRouter image API |
| 21 | Video understanding/generation | Partial | P2/V3 · video-capable tools/models |
| 22 | Universal input engine/common content representation | Partial | P1/V2 · file registry/content model |

### 23–32 · Memory & personalization

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 23 | Consent-based long-term memory | Implemented | P1/V2 · SQLite + opt-in |
| 24 | Conversation history search | Implemented | P1/V2 · SQLite history |
| 25 | Project-specific isolated memory | Partial | P2/V3 · project/workspace identity |
| 26 | User preference profile | Implemented | P0/V1 · user settings |
| 27 | Semantic memory retrieval | Planned | P3/V4 · embeddings/vector index |
| 28 | Knowledge graph for entities/relations | Planned | P4/V5 · graph schema |
| 29 | Memory confidence score/review | Planned | P3/V4 · evidence evaluation |
| 30 | Per-fact source provenance | Partial | P2/V3 · existing source user ID |
| 31 | Per-memory expiration/lifecycle | Partial | P2/V3 · expiry scheduler |
| 32 | User memory view/edit/export/delete controls | Implemented | P1/V2 · memory/data commands |

### 33–42 · Multi-agent network

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 33 | Chief/coordinator agent dispatches specialist review | Partial | P2/V3 · bounded agent orchestrator |
| 34 | Research agent prepares search-backed research | Partial | P2/V3 · web search/source policy |
| 35 | Coding agent reviews project and proposes patch | Partial | P2/V3 · ZIP workflow |
| 36 | Testing agent runs isolated approved tests | Partial | P2/V3 · approval + sandbox |
| 37 | Security agent reviews permission/dependency risks | Partial | P2/V3 · specialist role |
| 38 | Dedicated science tutor agent | Partial | P2/V3 · Study Mode; separate agent absent |
| 39 | File agent analyzes supported documents | Partial | P1/V2 · file adapters |
| 40 | Automation agent uses approved tools only | Partial | P2/V3 · tool registry + approval |
| 41 | Reviewer agent evaluates specialist drafts | Partial | P2/V3 · agent orchestrator |
| 42 | Consensus engine retains evidence and disagreement | Planned | P3/V4 · multi-agent protocol |

### 43–52 · Autonomous task execution

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 43 | Goal-to-result plan proposal | Implemented | P2/V3 · `/tasks plan` generates a bounded private task plan |
| 44 | Decompose goals into bounded steps | Implemented | P2/V3 · up to 10 validated steps with prerequisite links |
| 45 | Track task prerequisites/dependencies | Implemented | P2/V3 · per-user persistent task dependency graph |
| 46 | Show task/job progress | Partial | P2/V3 · personal task state in Telegram and Mini App; agent-job progress remains separate |
| 47 | Durable task checkpoints and resume | Planned | P2/V3 · persistent queue |
| 48 | Bounded retry and recovery | Partial | P2/V3 · idempotency/retry policy |
| 49 | Require approval before consequential actions | Implemented | P0/V1 · authorization |
| 50 | Reversible actions and rollback | Partial | P2/V3 · action undo contracts |
| 51 | Verify action postconditions | Partial | P2/V3 · result checks |
| 52 | Scheduled and recurring tasks/reminders | Implemented | P1/V2 · SQLite scheduler |

### 53–64 · Telegram workspace

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 53 | Smart `/start` category dashboard | Partial | P1/V2 · Telegram UX |
| 54 | Role/permission-aware dynamic commands | Partial | P2/V3 · per-role command menus |
| 55 | Inline settings/task controls | Partial | P2/V3 · Mini App task controls and settings; Telegram inline task keyboards absent |
| 56 | Telegram Mini App | Partial | P2/V3 · signed auth, themes, chat and personal task list implemented; full project/job dashboard remains |
| 57 | Live task status/progress dashboard | Partial | P2/V3 · durable queue |
| 58 | Rich streaming response | Implemented | P1/V2 · Bot API/client compatibility |
| 59 | Project-scoped file workspace | Partial | P2/V3 · persistent project store |
| 60 | Convert a selected message to a task | Implemented | P2/V3 · reply to a message with `/tasks add` |
| 61 | Convert chat into a project plan | Implemented | P2/V3 · `/tasks plan chat` converts bounded private history to a task plan |
| 62 | Unified personal notifications | Partial | P1/V2 · reminders; inbox absent |
| 63 | Permission-aware group assistant | Implemented | P1/V2 · group handlers |
| 64 | Telegram Business workflow integration | Planned | P3/V4 · Business API |

### 65–76 · Coding & self-evolution

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 65 | Codebase Explorer for supported project source | Partial | P2/V3 · source reader |
| 66 | Function/module dependency graph | Planned | P3/V4 · static analysis |
| 67 | Generate bug reproduction tests | Planned | P3/V4 · repro harness |
| 68 | Generate unit/integration/regression tests | Planned | P3/V4 · test agent + sandbox |
| 69 | Analyze test coverage | Planned | P3/V4 · coverage instrumentation |
| 70 | Generate reviewable patch proposal | Implemented | P2/V3 · ZIP patch workflow |
| 71 | Monitor dependency health/advisories | Planned | P2/V3 · dependency scanners |
| 72 | Generate docs from verified code | Planned | P3/V4 · code/API extraction |
| 73 | Plan architecture upgrades | Partial | P2/V3 · codebase analysis |
| 74 | Automated regression guard for changes | Partial | P2/V3 · test gate |
| 75 | Isolated sandbox experiments | Implemented | P2/V3 · rootless network-isolated runner |
| 76 | Approved, versioned self-evolution/rollout | Partial | P3/V4 · sandbox/patch; rollout absent |

### 77–88 · Security, privacy & reliability

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 77 | Per-action zero-trust permission checks | Partial | P0/V1 · authorization policy |
| 78 | User authentication/admin authorization | Implemented | P0/V1 · Telegram/dashboard identity |
| 79 | Dedicated encrypted secret vault | Partial | P2/V3 · env secrets exist; vault absent |
| 80 | Continuous secret leak detection/alert | Planned | P2/V3 · scanner + alerting integration |
| 81 | Rate limiting and abuse protection | Implemented | P0/V1 · configured limits |
| 82 | Treat prompt/document inputs as untrusted | Implemented | P0/V1 · prompt/input boundaries |
| 83 | Isolate tool execution | Implemented | P0/V1 · restricted sandbox |
| 84 | Tamper-evident immutable audit ledger | Partial | P1/V2 · SQLite audit hash chain detects row changes; no external immutable anchor, and privacy deletion re-chains retained rows |
| 85 | Privacy mode and local-only processing | Implemented | P0/V1 · local Ollama |
| 86 | User data export/delete controls | Implemented | P1/V2 · database lifecycle |
| 87 | Provider/API health and outage monitoring | Partial | P1/V2 · probes; full alerting absent |
| 88 | Disaster recovery/restore drills | Partial | P2/V3 · encrypted backups; drills absent |

### 89–100 · Automation & digital infrastructure

| ID | Feature / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 89 | Unified registered tools and permissions | Implemented | P0/V1 · action registry |
| 90 | Visual/natural-language workflow builder | Partial | P3/V4 · private-chat, read-only mission planning exists; no general workflow editor |
| 91 | General event-driven automation | Partial | P2/V3 · triggers/reminders; event bus absent |
| 92 | Local-first processing when configured | Implemented | P0/V1 · Ollama/privacy policy |
| 93 | Controlled hybrid provider failover | Partial | P1/V2 · cloud failover; local-to-cloud blocked by policy |
| 94 | Provider circuit breaker/cool-off | Implemented | P1/V2 · bounded provider circuit breaker |
| 95 | Durable queue-based background jobs | Partial | P2/V3 · encrypted Mission records/checkpoints exist; no distributed queue |
| 96 | Resource-aware task scheduling | Partial | P2/V3 · metrics; scheduling policy absent |
| 97 | Service/database/tool self-diagnostics | Partial | P1/V2 · current status checks |
| 98 | Latency/error/success observability dashboard | Partial | P1/V2 · dashboard metrics |
| 99 | Approved multi-device integration | Partial | P2/V3 · allowlisted HA/VM |
| 100 | Cross-channel task continuity | Planned | P3/V4 · shared task protocol/identity |

### 101–120 · Frontier research concepts

এগুলো experimental roadmap item; `Planned` status কোনো demonstrated capability বা বৈজ্ঞানিক নিশ্চয়তা নয়।

| ID | Concept / completion outcome | Status | Phase / main dependency |
|---:|---|---|---|
| 101 | AI Digital Twin Lab — compare simulated and tested outcomes | Planned | P4/V5 · reliable system simulator |
| 102 | Agent Civilization — governed dynamic agent task allocation | Planned | P4/V5 · agent protocol |
| 103 | Intent Compiler — natural language to permissioned workflow graph | Planned | P4/V5 · workflow DSL/policy engine |
| 104 | Future Scenario Engine — compare assumptions, costs and risks | Planned | P4/V5 · evidence-backed scenario model |
| 105 | Memory Provenance Universe — source/time/dependency per decision | Partial | P4/V5 · limited source metadata |
| 106 | Self-Healing Software Organism — diagnose/propose/test/approve/rollback | Partial | P4/V5 · encrypted diff proposals, isolated tests, hash approvals; no production deploy/rollback |
| 107 | Personal AI Constitution — persistent allow/deny/approval policy | Partial | P2/V3 · existing rules; unified policy absent |
| 108 | Holographic Telegram Command Center | Planned | P4/V5 · 3D UI/telemetry; adaptive 2D Mini App exists |
| 109 | Evolution Tournament — benchmark competing patch candidates | Planned | P4/V5 · isolated evaluation harness |
| 110 | Privacy-Preserving Intelligence Mesh | Planned | P4/V5 · trusted multi-node policy routing |
| 111 | Agent Trust Passport — verifiable identity/capabilities/audit | Planned | P4/V5 · signed agent registry |
| 112 | Semantic Memory Compressor — compact, source-faithful retrieval | Planned | P4/V5 · semantic index/fidelity tests |
| 113 | Time-Travel Debugger — correlate versions, jobs, decisions, logs | Planned | P4/V5 · versioned event store |
| 114 | Intent Radar — suggest likely needs without acting silently | Planned | P4/V5 · consent-aware intent evaluation |
| 115 | AI Council with Dissent — preserve competing hypotheses/evidence | Planned | P4/V5 · independent agent protocol |
| 116 | Capability Synthesizer — compose pre-approved tools only | Planned | P4/V5 · tool registry/workflow verifier |
| 117 | Continuity Mode — bounded offline work and safe resync | Partial | P4/V5 · local-only exists; offline queue absent |
| 118 | Predictive Reliability Engine — probabilistic pre-failure alert | Planned | P4/V5 · telemetry/calibrated model |
| 119 | Continuous Knowledge Evolution — sourced, versioned conflict handling | Partial | P4/V5 · opt-in extraction; verification absent |
| 120 | Unified Intelligence Operating System — shared task/memory/policy protocol | Partial | P4/V5 · modules integrated; protocol absent |

### Suggested delivery phases

| Phase | Focus | Dependencies | Exit criteria |
|---|---|---|---|
| V1 Foundation | Telegram, provider routing, privacy, auth, limits, logs | Current runtime/database/configuration | Provider errors bounded; authorization/privacy tests pass |
| V2 Multimodal + Workspace | Voice/image/files, memory UX, rich messages, job visibility | V1 + supported model/API | Modality, consent, export/delete and local-mode tests pass |
| V3 Multi-Agent Automation | Durable jobs, dependencies, specialist agents, approvals/rollback | V1/V2 + durable queue/policy | Idempotent retry, isolation, approval, recovery and audit tests pass |
| V4 Self-Evolution Lab | Test/coverage generation, candidate benchmarks, canary/rollback | V3 + reproducible sandbox/release pipeline | No production edit without approval; rollback exercised |
| V5 Frontier Research | Digital twin, semantic memory, intent compiler, predictive reliability | Stable V1–V4 telemetry and governance | Measured, source-attributed results; independent evaluation |

### Registry and safety requirements

- IDs are stable references for issues/tasks. Before implementing an item, record an owner, exact dependencies, actual API/provider/model requirements, data classification, security level and measurable acceptance tests.
- Any item that reads private data, stores memory, controls devices, executes tools, changes files or deploys code must define consent, authorization, retention, audit and rollback tests first.
- A model nickname does not prove API availability. Do not silently upload private data, execute arbitrary shell, apply unapproved production changes, or describe unverified self-improvement as training.

## 12. Verification notes

Feature catalog code/documentation-এর capability overview; deployment-specific smoke test নয়। Provider-dependent behavior পরীক্ষা করতে valid credentials/model ও non-production Telegram bot দিয়ে manual test করুন। Sensitive user text background extractor-এ পাঠানোর আগে `/memory on` দিয়ে memory ও learning দুটোই চালু করতে হবে; memory আগে থেকেই on থাকলে `/memory learn on` আলাদাভাবে চালু করা যায়।
