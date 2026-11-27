# AI provider setup

Copy `.env.example` to `.env` with `python scripts/init_env.py`, then configure one or more providers. Keep credentials private and use an OS-protected environment file. The bot never returns configured key values through Telegram commands or logs.

## Provider credentials

| Provider | Credentials | Required model/account settings | Default endpoint |
| --- | --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` or `OPENROUTER_API_KEYS` | `OPENROUTER_MODEL` or `DEFAULT_AI_MODEL` | `https://openrouter.ai/api/v1` |
| Groq | `GROQ_API_KEY` or `GROQ_API_KEYS` | `GROQ_MODEL` | `https://api.groq.com/openai/v1` |
| Gemini | `GEMINI_API_KEY` or `GEMINI_API_KEYS` | `GEMINI_MODEL` | `https://generativelanguage.googleapis.com/v1beta` |
| Cloudflare Workers AI | `CLOUDFLARE_API_TOKENS`; legacy aliases `CLOUDFLARE_API_KEYS` and `CLOUDFLARE_API_KEY` also work | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_MODEL` | Account-scoped Workers AI endpoint derived from the account ID |
| Hugging Face Inference Providers | `HUGGINGFACE_API_KEY` or `HUGGINGFACE_API_KEYS` | `HUGGINGFACE_MODEL` | `https://router.huggingface.co/v1` |

Plural key variables are comma-separated, allow up to ten non-empty credentials per provider, and take precedence over the singular variable when non-empty. Cloudflare precedence is `CLOUDFLARE_API_TOKENS`, then `CLOUDFLARE_API_KEYS`, then `CLOUDFLARE_API_KEY`. The first key is used first; subsequent requests start at the next configured key. Rotation is for invalid credentials only, not quota sharing.

Choose a model identifier supported by the provider account and endpoint. A model identifier being accepted by the bot does not prove that the provider account has access, that the model is free, or that it supports tools, images, or streaming.

## Routing policy

`AI_PROVIDER_ORDER` is a comma-separated ordered list of unique supported cloud providers. The default is:

```dotenv
AI_PROVIDER_ORDER=openrouter,groq,gemini,cloudflare,huggingface
```

Only configured providers are eligible. Put a preferred model in `OPENROUTER_MODEL` or `DEFAULT_AI_MODEL`; use `provider::model-id` to target another provider. `FALLBACK_MODELS` contains model IDs tried before the router's configured provider models.

## Cloudflare token setup

Create a Cloudflare API token with the Workers AI permission required by your account, then set `CLOUDFLARE_API_TOKENS` and the 32-character hexadecimal `CLOUDFLARE_ACCOUNT_ID`. The default endpoint is derived from the account ID. Set `CLOUDFLARE_BASE_URL` only when using a compatible custom endpoint. Set the full Workers AI model ID in `CLOUDFLARE_MODEL`.

## Verify configuration safely

Start the bot and use `/providers` as an admin to see provider health and model-list probe latency. `/providerdiag` reports locally known circuit state, credential counts, quarantine counts, and cooldown without displaying values. If a credential was rotated or access has been restored, use `/providerdiag recheck <provider>` to clear the local quarantine and perform a provider model-list probe.

Read the provider's current account limits and billing page before enabling a key. They can depend on project, account, model, and billing tier and can change without a bot release. Official references: [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits), [Groq rate limits](https://console.groq.com/docs/rate-limits), [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [Cloudflare Workers AI limits](https://developers.cloudflare.com/workers-ai/platform/limits/), and [Hugging Face Inference Providers pricing](https://huggingface.co/docs/inference-providers/pricing).
