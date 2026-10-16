# Multimodal request architecture

Incoming Telegram updates are dispatched by `BotHandlers.on_message()`:

1. Text is passed unchanged to the normal context-aware chat path.
2. Photos are downloaded within configured limits, validated, normalized where applicable, and sent as image parts to the configured vision route.
3. Voice/audio is size-checked, transcribed by the configured audio-capable route, then passed as text to normal chat.
4. Video is duration/frame bounded by `MediaProcessor`; selected frames and, when enabled and available, extracted audio/transcript are passed to chat.
5. Documents are scanned (when ClamAV is enabled), signature/type-routed to local PDF, Office, image/OCR, archive, or text adapters, and relevant extracted text/media is attached to the ordinary chat request.

Provider adapters and `ProviderRouter` enforce modality, model capabilities, privacy mode, request attempt limits and cost/free-model policy. A compatible fallback may receive the same message context; the bot does not treat all providers as universal file readers. If local extraction fails, remote file fallback is an explicit server configuration, not an implicit guarantee.

Relevant implementation: `handlers.py`, `files/registry.py`, `services/media.py`, `services/providers.py`, `services/openrouter.py`. See [voice](./VOICE_INTELLIGENCE.md), [document processing](./DOCUMENT_PROCESSING.md), [provider capabilities](./PROVIDER_CAPABILITIES.md), and [privacy/retention](./PRIVACY_AND_FILE_RETENTION.md).
