# Provider failover and retry behavior

The router bounds retries so one Telegram request cannot loop indefinitely or consume unbounded provider attempts. Defaults are configurable in `.env`.

| Setting | Default | Meaning |
| --- | ---: | --- |
| `AI_REQUEST_TIMEOUT` | `30` seconds | Timeout for one provider operation |
| `AI_MAX_RETRIES_PER_KEY` | `1` | Same-key retries for network/temporary failures; allowed range is 0–3 |
| `AI_MAX_TOTAL_ATTEMPTS` | `5` | Maximum provider POST attempts across the request, including same-key retries and credential changes |
| `AI_CIRCUIT_BREAKER_THRESHOLD` | `3` | Consecutive temporary failures before opening a provider circuit |
| `AI_CIRCUIT_BREAKER_COOLDOWN` | `60` seconds | Default transient/quota cooldown |

## Status handling

- **401 / confirmed invalid credential:** quarantine that credential and try another configured credential. Quarantine is stored as a SHA-256 fingerprint beside `DATABASE_PATH`, not as a key. When every configured credential is rejected, the router marks that provider unavailable until an admin recheck.
- **403:** rotate only when the provider error clearly identifies an invalid credential. Permission, account-plan, or model-access errors are not assumed to mean the key itself is invalid.
- **402 quota/billing and 429 rate limit:** do not try sibling keys. These limits are often shared by an account or project. Stop requests to that provider during cooldown, respect `Retry-After` or provider reset headers when supplied, then continue to the next eligible provider/model for the current request.
- **408, 425, 5xx, network failures:** bounded exponential backoff with jitter retries the same credential. When the configured failure threshold is reached, the provider circuit opens for the cooldown period.
- **Invalid request or unsupported model:** do not keep retrying the same request shape against the same adapter. Model/capability filtering runs before chat requests where possible.

Successful requests close the provider circuit. A `429` cooldown uses the longer of the configured cooldown and the provider retry hint. Quota/billing cooldown is at least five minutes unless an upstream retry hint requires longer. The provider cooldown is in memory; credential quarantine persists across process restarts.

## Attempts, streaming, and user-visible errors

Each outbound provider POST consumes one shared attempt from `AI_MAX_TOTAL_ATTEMPTS`; catalog GETs do not. A request may continue to another eligible provider when a call fails before output is sent. If a stream has already emitted text, the bot does not replay the prompt through another provider, avoiding duplicate answers. A partial stream failure is reported as interrupted.

Provider error bodies are inspected only for a small set of classification terms. They are not included in logs, admin alerts, or end-user errors. Logs include provider, normalized category, latency on success, and a correlation ID. Admin alerts omit prompts and key values and are rate-limited.

## Diagnostics and recovery

- `/providers` performs a health/model-list probe, cached for up to 30 seconds by provider adapter.
- `/providerdiag` reads local breaker and credential counts without network access.
- `/providerdiag recheck <provider>` clears that provider's rejected-key quarantine/cooldown and makes a fresh model-list request. If the provider still rejects all credentials, those keys are quarantined again.

Provider-side fallback still depends on account access, model availability, account quota, and provider outage state. A fallback cannot guarantee that another provider accepts the same modality, tool request, model, or billing policy.
