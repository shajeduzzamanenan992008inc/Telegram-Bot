# Enterprise GitHub Security Hardening Guide

This document is a configuration checklist, not a claim that security controls are already enabled. GitHub features and plan entitlements change; verify availability in the target organization's current GitHub settings and official documentation.

## 1. Repository security settings

Review and enable where available and appropriate:

- Dependency Graph and Dependabot Alerts.
- Dependabot Security Updates.
- Secret Scanning and Push Protection.
- Code Scanning / CodeQL or an approved alternative.
- Private vulnerability reporting.
- Rulesets / branch protection and required pull-request reviews.
- Restrict GitHub Actions to approved actions and reusable workflows.
- Require full-length SHA pins or approved immutable action lockfiles where supported.

Native repository settings cannot be activated by committing this ZIP. An authorized repository/org owner must configure them.

## 2. Default branch protection

For the actual default branch (do not assume it is `main`):

- Require pull requests and the appropriate number of independent approvals.
- Require review from code owners for sensitive paths where applicable.
- Require only checks that exist, are stable, and have completed successfully.
- Require conversation resolution and signed commits if organizational policy requires them.
- Block force pushes and branch deletion; restrict bypass to a minimal audited group.
- Apply rules consistently to administrators where practical.
- Revisit required checks when workflows, matrix names, or project stack change.

## 3. GitHub Actions controls

- Grant `GITHUB_TOKEN` the minimum permissions needed; set them explicitly at workflow and job scope.
- Pin every third-party action to a verified full-length commit SHA or use an approved immutable workflow dependency lockfile.
- Verify action origin, maintainers, source code, transitive dependencies, and requested permissions.
- Do not pass secrets to pull-request workflows or untrusted forks.
- Avoid privileged `pull_request_target`; never execute untrusted PR code in privileged `workflow_run` contexts.
- Use protected environments, required reviewers, branch/tag restrictions, and OIDC/short-lived credentials for deployment when supported.
- Set timeouts, concurrency, artifact retention, and safe cache scopes.
- Separate build/test from deployment and verify exact artifact/commit identity.

## 4. Secrets and identity

- Store credentials in GitHub Environments or an approved secret manager; scope secrets to the smallest environment/job.
- Prefer short-lived workload identity federation over long-lived cloud keys when the provider supports it.
- Rotate exposed credentials through a coordinated incident/change process; do not simply delete values from the latest commit and assume history is safe.
- Never print secrets, include them in issue reports, or put them in artifacts/caches.
- Use dedicated identities for automation and periodically review access grants.

## 5. Supply chain

- Commit lockfiles where supported and install reproducibly in CI.
- Enable Dependabot/renovate or an approved dependency update process for all actual ecosystems.
- Review vulnerabilities for exploitability and runtime reachability, not severity alone.
- Assess dependency license obligations and provenance before distribution.
- Produce SBOMs/provenance attestations when product risk, customers, or policy requires them.
- Protect release signing keys and verify artifact digests before publication/deployment.

## 6. Production operations

- Use separate development, test, staging, and production environments where warranted.
- Require approval for production changes and data-destructive operations.
- Define backup scope, retention, encryption, restore tests, recovery owner, RPO, and RTO.
- Monitor errors, latency, saturation, security signals, data integrity, and critical user journeys.
- Maintain incident response, security incident, release, migration, and restore runbooks.

## 7. Evidence and review

Record the control owner, scope, setting, date verified, evidence link, exceptions, and review date. A checklist is not evidence of control operation. Do not claim SOC 2, ISO 27001, GDPR, HIPAA, PCI DSS, or any other compliance status solely from repository settings; use qualified legal/compliance/security review.

## Official references

- [GitHub secure use of Actions](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub repository rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [Dependabot supported ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories)
- [GitHub secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)
- [GitHub Code Scanning](https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning)
