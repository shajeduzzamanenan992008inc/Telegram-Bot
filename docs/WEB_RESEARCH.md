# Web research

The bot has two user-triggered web paths:

- `/search <query>` fetches a bounded set of DuckDuckGo snippets and asks the configured OpenRouter integration to synthesize an answer with source URLs.
- `/read <https-url>` fetches and cleans a public page after URL/host/redirect/size/time validation, then asks the configured model to summarize extracted page text.

Chat web search can also be enabled by server `WEB_SEARCH_ENABLED=true` and the user's `/web on` preference. The provider router requires a configured OpenRouter model with tool capability and enforces the active cost/free policy. Local-only mode does not perform remote web research.

Search snippets and fetched page content are not proof that a claim is true. The bot must not invent citations or claim a live search occurred when the configured route was unavailable. `/read` accepts user-supplied public HTTPS URLs only; it is not a crawler or monitoring service. See [provider capabilities](./PROVIDER_CAPABILITIES.md), [configuration](./CONFIGURATION.md), and [troubleshooting](./TROUBLESHOOTING.md).
