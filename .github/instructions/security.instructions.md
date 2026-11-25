---
applyTo: "**/*{auth,security,token,session,secret,crypto,permission,policy,payment,upload,webhook}*.*,**/.github/workflows/**,**/infra/**,**/infrastructure/**"
---

# Security-sensitive engineering instructions

- Map the asset, actor, trust boundary, privileged operation, sensitive data, and abuse case before changing security-sensitive code.
- Treat user input, external responses, uploaded files, dependency output, logs, and tool results as untrusted.
- Review broken access control/IDOR, injection, SSRF, XSS, CSRF, path traversal, unsafe deserialization, replay, rate limiting, and resource exhaustion as relevant.
- Validate and authorize on the server or trusted enforcement point; do not rely on UI controls or client state.
- Use least privilege, secure defaults, safe cryptographic primitives, and approved secret stores.
- Never print or commit secret values or real customer data. Preserve auditability for sensitive state changes.
- Do not disable security controls to make a test pass. Explain and fix the root cause.
- Do not run production exploitation, destructive tests, or broad scanning without explicit scope and authorization.
