# Voice input and output

Voice input requires `VOICE_INPUT_ENABLED=true`, a compatible configured audio transcription route, and remote/cloud privacy permission. Telegram voice notes and supported audio documents are downloaded under `MAX_UPLOAD_BYTES`; `ffprobe` validates duration against `MAX_AUDIO_SECONDS`, and transcription runs through the configured router. A nonempty transcript is shown to the sender (capped in the display) and becomes the prompt for normal AI conversation and history handling. Failed or empty transcription is not presented as successful text.

In local-only privacy mode, remote transcription is refused rather than silently sending audio to a cloud service. `/voice on|off` controls per-user voice replies, not the server-side voice-input switch.

Voice output is governed by `VOICE_OUTPUT_MODE`, the per-user `/voice` preference, `TTS_MODEL`, and `TTS_VOICE`. Configured OpenRouter TTS is tried first; the documented gTTS fallback sends answer text to Google's external service. Local-only mode disables remote TTS. Only generated nonempty audio within the configured upload limit is sent; temporary MP3 files are removed after use.

Model availability, supported languages, speech quality and price are provider-dependent. See [configuration](./CONFIGURATION.md), [provider capabilities](./PROVIDER_CAPABILITIES.md), and [troubleshooting](./TROUBLESHOOTING.md).
