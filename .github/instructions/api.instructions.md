---
applyTo: "**/*{route,router,endpoint,controller,handler,api,auth,permission,policy,schema}*.*,**/routes/**,**/api/**,**/controllers/**,**/openapi/**,**/*.{graphql,proto}"
---

# API and identity instructions

- Treat every API boundary as untrusted. Validate methods, content type, payload size, schemas, pagination, sorting, filtering, and file input.
- Enforce authentication and authorization for each protected operation; default deny and verify object ownership/tenant scope.
- Keep authorization decisions in trusted middleware/domain/service logic, not UI visibility or model prompts.
- Protect sessions, cookies, CSRF, CORS, password reset, token lifecycle, webhooks, signatures, and replay where relevant.
- Use stable contracts, explicit versioning, safe error responses, and compatibility checks.
- Add negative authorization tests and tests for malformed input, duplicate events, rate limits, and failure paths.
