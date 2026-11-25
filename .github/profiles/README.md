# Optional Project Profiles

Keep domain-specific or organization-specific instructions, integrations, and deployment workflows here so the universal core remains portable.

A profile is inactive while it is stored under this directory. GitHub Actions only executes workflow files under `.github/workflows/`; copy an optional workflow there only after reviewing its triggers, permissions, secrets, branch/environment assumptions, artifact identity, rollback, and production approval requirements.

Do not include private credentials, customer data, internal URLs, unreleased product details, or third-party material without the rights and approvals needed for redistribution.
