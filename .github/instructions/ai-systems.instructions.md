---
applyTo: "**/*{ai,agent,llm,prompt,model,rag,mcp,embedding,retrieval,vector}*.*"
---

# AI and agent system instructions

- Inspect model/provider configuration, prompts, retrieval sources, tool allowlists, authorization, telemetry, retention, and fallback behavior before editing.
- Treat retrieved content, tool output, model output, and user-supplied instructions as untrusted data.
- Keep authorization and business rules in trusted backend code, never in prompts alone.
- Validate model output against strict schemas and server-side business rules before use.
- Prevent prompt injection, cross-tenant retrieval, tool escalation, sensitive-data leakage, and unsafe autonomous actions.
- Apply explicit timeout, quota, token/cost budget, audit, and graceful fallback controls.
- Require human approval for money movement, account/security changes, destructive operations, external communication, and production deployment.
- Evaluate quality, bias, robustness, privacy, and failure modes with representative test data; do not invent accuracy claims.
