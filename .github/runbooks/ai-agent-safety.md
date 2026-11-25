# AI and Agent Safety Runbook

## Before enabling a capability
- Inventory model/provider, prompt versions, data sources, retrieval scope, tools, identities, network egress, telemetry, and side effects.
- Define who may invoke each capability and which server-side policy authorizes it.
- Treat all prompts, retrieved content, model responses, tool output, and connected-app data as untrusted.
- Set explicit data retention, privacy boundaries, tool allowlists, quotas, timeouts, and cost budgets.

## Safety verification
- Test direct and indirect prompt injection, data exfiltration, cross-tenant retrieval, tool escalation, malformed model output, replay, and rate abuse.
- Validate arguments and output schemas before calling tools or changing persistent state.
- Require human approval for consequential or externally visible actions.
- Test timeout, model/provider outage, empty retrieval, refusal, and safe fallback behavior.

## Operational response
- Disable the specific unsafe tool or capability through the approved control plane.
- Preserve privacy-safe audit evidence and identify affected users/data.
- Follow the security/privacy incident process when a boundary was crossed.
- Re-enable only after a reviewed fix and regression evidence.
