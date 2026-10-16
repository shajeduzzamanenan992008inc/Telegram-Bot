# AI provider routing architecture

The bot routes chat requests through the configured provider adapters. The router checks privacy mode, model availability, declared capabilities, free-model policy, estimated cost, a per-request attempt budget, and provider circuit state before sending a request.

```mermaid
flowchart LR
    A[Telegram request] --> B{Privacy mode}
    B -->|Local only| C[Ollama only]
    B -->|Cloud allowed| D[Primary model]
    D --> E[AI_PROVIDER_ORDER]
    E --> F[Capability and free/cost policy]
    F --> G[Attempt budget and circuit breaker]
    G --> H[Provider adapter]
    H -->|Success| I[AssistantResult]
    H -->|Temporary failure| J[Eligible next model/provider]
    J --> F
```

## Supported providers

The configured cloud router supports OpenRouter, Groq, Gemini, Cloudflare Workers AI, and Hugging Face Inference Providers. The order defaults to `openrouter,groq,gemini,cloudflare,huggingface`; `AI_PROVIDER_ORDER` changes that order. A provider must also have its credentials, endpoint/account details, and model configured before it can be selected. Local Ollama is an optional route and is used by local-only privacy mode.

OpenRouter also supplies model catalog, pricing, web search, and media/file operations. The other four chat adapters use their provider APIs directly. Direct provider credentials and OpenRouter credentials are independent; routing through Hugging Face or OpenRouter does not make the bot's local cost estimate equivalent to the provider's invoice.

## Model eligibility and capabilities

- `/model provider::model-id` selects a preferred model. Unqualified IDs refer to OpenRouter.
- A model must accept the requested input modality. Tool use, image input, and streaming require provider catalog evidence or an explicit qualified-model setting (`AI_TOOL_CAPABLE_MODELS`, `AI_IMAGE_CAPABLE_MODELS`, `AI_STREAMING_MODELS`).
- OpenRouter catalog capability and price data can change. Direct adapters only claim optional capabilities that an operator explicitly lists.
- In `AI_FREE_ONLY=true` mode, only models in `AI_FREE_MODEL_ALLOWLIST` are eligible for cloud routing. Each entry is `provider::model-id`. Local Ollama remains available in local-only mode.
- `AI_ALLOW_PAID_FALLBACK=true` permits non-allowlisted candidates only when the configured estimated-cost cap is positive and the candidate has an acceptable estimate. Unknown price is not approved for paid fallback.

## Request metadata and privacy

Successful chat results include provider, model, elapsed time, and a generated correlation ID. Logs and admin failure alerts contain provider/category/correlation metadata, not prompts, completions, or credential values. `/providerdiag` reads local circuit and credential-health state without probing providers. `/providers` performs the provider's cached health probe; `/providerdiag recheck <provider>` explicitly runs a fresh model-list check.

The key health file beside `DATABASE_PATH` stores only SHA-256 fingerprints of rejected credential values. It allows rejected keys to remain quarantined after restart without writing the keys themselves. Protect this state file like other application state.

See [provider setup](PROVIDER_SETUP.md), [retry and failover](FAILOVER_AND_RETRY.md), and [free-tier and cost controls](FREE_TIER_AND_COST_CONTROL.md).
