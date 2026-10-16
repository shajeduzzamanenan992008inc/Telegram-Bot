# Free-model and cost controls

Provider free access is account- and model-specific. “Free” status can change, limits can be shared across keys, and providers may require billing or paid plans for a model. The router does not hard-code provider free-tier quota numbers or promise that an allowlisted model remains free. Check provider dashboards and current terms before routing production traffic.

## Strict free-only routing

Set `AI_FREE_ONLY=true` and list the exact model IDs you have approved:

```dotenv
AI_FREE_ONLY=true
AI_FREE_MODEL_ALLOWLIST=openrouter::vendor/model:free,groq::free-model-id
```

Entries use `provider::model-id`, are case-insensitive for matching, and should match the provider's model identifier exactly. In this mode cloud candidates not in the allowlist are skipped, including configured defaults and `FALLBACK_MODELS`. An allowlist is the operator's policy decision; it is not a live price guarantee from the provider.

## Paid fallback with an estimate cap

Paid fallback is opt-in and requires both settings:

```dotenv
AI_ALLOW_PAID_FALLBACK=true
AI_MAX_ESTIMATED_COST_USD=0.05
```

With free-only mode enabled, non-allowlisted fallback candidates are eligible only when the router can estimate their cost within the positive cap. OpenRouter catalog pricing is used where available. For other direct providers, `PROVIDER_REQUEST_RESERVE_USD` supplies a conservative per-candidate reserve. Unknown OpenRouter prices are not approved as paid fallback. The router sums eligible candidate estimates for its preflight check, so a long fallback chain can be rejected before the first model call if its total estimate exceeds the cap.

These are estimates, not provider billing guarantees. Tokenization, provider-side routing, retries, plugins, image pricing, billing tiers, and catalog changes may make the final charge differ. Set account/project spend caps at the provider as a separate safeguard. Existing user and daily usage limits remain in force.

## Capability allowlists

Direct provider models only claim tools, image input, or streaming when explicitly listed:

```dotenv
AI_TOOL_CAPABLE_MODELS=groq::tool-model-id
AI_IMAGE_CAPABLE_MODELS=gemini::vision-model-id
AI_STREAMING_MODELS=groq::streaming-model-id
```

OpenRouter capabilities are read from its current model catalog when available. Keep each list current with the provider's documentation/model page. Image input refers to model input capability; it does not enable image generation.

## Relevant upstream pages

- [OpenRouter limits and Retry-After behavior](https://openrouter.ai/docs/api_reference/limits)
- [Groq rate limits and response headers](https://console.groq.com/docs/rate-limits)
- [Gemini API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Cloudflare Workers AI limits](https://developers.cloudflare.com/workers-ai/platform/limits/) and [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [Hugging Face Inference Providers billing](https://huggingface.co/docs/inference-providers/pricing)
