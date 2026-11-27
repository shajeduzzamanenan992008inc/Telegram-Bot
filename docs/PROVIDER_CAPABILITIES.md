# Provider capabilities and routing

The bot can route through configured OpenRouter, Groq, Google AI Studio/Gemini, Cloudflare Workers AI, Hugging Face Inference API, and local Ollama adapters. Configuration does not mean every configured model supports every modality.

`ProviderRouter` filters candidate models by requested input modality, tool support, explicit capability settings/model metadata, privacy mode, provider health, attempt limit, and free/cost policy. `VISION_MODEL` selects the OpenRouter vision route for Telegram photo input when that provider is available; model capability must still be present. Audio transcription uses the configured STT/audio route. Server-side web search requires OpenRouter and a tool-capable model. Local-only mode does not fall through to cloud.

Use `/providers`, `/models`, `/model`, and admin `/providerdiag` to inspect runtime availability without exposing credentials. Model catalogs, account quota, region access and prices can change. No static provider name guarantees a model feature or Bengali/audio/vision quality. For setup and cost behavior, see [provider setup](./PROVIDER_SETUP.md), [architecture](./AI_PROVIDER_ARCHITECTURE.md), [failover](./FAILOVER_AND_RETRY.md), and [free-tier/cost controls](./FREE_TIER_AND_COST_CONTROL.md).
