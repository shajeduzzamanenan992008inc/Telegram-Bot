---
applyTo: "**/*.{tf,tofu,hcl,yml,yaml,json},**/Dockerfile*,**/docker-compose*,**/compose.*,**/k8s/**,**/kubernetes/**,**/helm/**,**/charts/**,**/infra/**,**/infrastructure/**,**/.devcontainer/**"
---

# Infrastructure and platform engineering instructions

- Inspect the current cloud/platform, environments, identity model, network boundaries, state backends, and deployment process before editing.
- Prefer declarative, reviewable, reproducible infrastructure and immutable build artifacts.
- Apply least-privilege IAM, private networking where required, encryption, safe defaults, and explicit environment boundaries.
- Protect state files and plan outputs; never commit credentials, state secrets, or environment-specific private data.
- Review blast radius, drift, cost, quotas, backups, dependencies, and rollback before applying changes.
- Separate plan/preview from apply/deploy. Production changes require explicit approval and protected environments.
- Pin container base images and actions according to the organization's supply-chain policy; scan and minimize runtime images.
- Do not claim resilience, compliance, or cost savings without measurements and evidence.
