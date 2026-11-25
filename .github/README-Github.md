# Universal Enterprise Engineering Kit

**Repository engineering framework · Stack-agnostic · Configurable CI**

A reusable GitHub engineering system for software teams and independent developers. It provides repository-wide AI engineering instructions, specialist review agents, path-specific standards, security and operations runbooks, reusable prompts, contribution templates, dependency-management examples, and a profile-driven CI entry point.

**It is a configurable framework, not an automatically production-ready application.** It does not make a project compliant, secure, scalable, or enterprise-ready by itself. Teams must connect real project commands, repository settings, architecture, controls, evidence, and operations.

## 1. Project coverage

The framework is domain-neutral and can be adapted to:

- Web applications, websites, SaaS products, mobile apps, desktop applications, APIs, backend services, and microservices.
- Enterprise platforms, internal business systems, commerce/marketplaces, CRM/ERP, finance, education, health, media, logistics, and public-sector software.
- AI/LLM applications, agents, RAG, automation, data engineering, analytics, and machine-learning systems.
- Cloud-native systems, containers, serverless, infrastructure-as-code, DevOps, platform engineering, and SRE.
- CLI tools, developer tools, libraries, SDKs, game software, IoT, embedded, and hardware-adjacent projects.

It includes guidance for Python, JavaScript/TypeScript, Java/Kotlin, Go, Rust, C/C++, C#/.NET, PHP, Ruby, Dart/Flutter, Swift, SQL, Docker, Kubernetes, Terraform/OpenTofu, and mixed monorepos. The project must still select its actual language versions, commands, operating systems, deployment providers, and test environments.

## 2. Package layout

```text
.github/
├── README.md
├── copilot-instructions.md
├── project-profile.json
├── dependabot.yml
├── SECURITY-HARDENING-GUIDE.md
├── PULL_REQUEST_TEMPLATE.md
├── CODEOWNERS.example
├── agents/                 # 9 reusable specialist agents
├── instructions/           # Path-aware engineering standards
├── prompts/                # Repeatable audit, implementation and release prompts
├── skills/                 # Task playbooks; discovery depends on the AI host
├── runbooks/               # Incident, migration, release, backup and AI safety
├── scripts/                # Profile runner and configuration validation
├── workflows/              # Safe, generic CI and configuration validation
├── templates/              # Optional workflow, Dependabot and policy templates
├── PRODUCT-LAUNCH-CHECKLIST.md
├── KIT-VALIDATION-REPORT.md
├── profiles/               # Optional domain-specific profiles, inactive by default
└── licensing/              # Commercial-license starting template
```

## 3. Install into a repository

1. Back up the repository's existing `.github` directory and inspect local changes.
2. Extract this ZIP into the repository root, not into `.github/.github`.
3. Review the diff and merge files selectively if the repository already has workflows, policies, issue templates, or instructions.
4. Edit `.github/project-profile.json` and replace `CHANGE-ME` with the actual project name, purpose, type, platforms, languages, data classification, and deployment targets.
5. Add only real CI commands to `ci.checks` (examples below). Verify every command locally first.
6. Add only the Dependabot ecosystems whose manifests/lockfiles actually exist.
7. Configure branch rulesets, environments, secrets, Actions policy, code scanning, and repository access in GitHub Settings.
8. Run CI on a feature branch, review every result, then merge under the project's normal approval process.

Do not overwrite a live deployment workflow with a template. Existing workflow ownership, secret names, cloud environments, and release behavior must be reviewed before migration.

## 4. Configure project-specific checks

`.github/project-profile.json` uses explicit argument arrays; no shell interpolation is performed by the profile runner. Configure only commands that are valid in the target repository and GitHub-hosted runner environment.

Example Python project:

```json
"checks": [
  {"name": "Python unit tests", "command": ["python", "-m", "pytest", "-q"], "timeoutSeconds": 900}
]
```

Example Node project:

```json
"checks": [
  {"name": "Install locked packages", "command": ["npm", "ci"], "timeoutSeconds": 900},
  {"name": "Test", "command": ["npm", "test", "--if-present"], "timeoutSeconds": 900},
  {"name": "Build", "command": ["npm", "run", "build", "--if-present"], "timeoutSeconds": 1200}
]
```

Example Go project:

```json
"checks": [
  {"name": "Go tests", "command": ["go", "test", "./..."], "timeoutSeconds": 1200}
]
```

Example Rust project:

```json
"checks": [
  {"name": "Rust tests", "command": ["cargo", "test", "--all-targets"], "timeoutSeconds": 1800}
]
```

For Java, Kotlin, .NET, PHP, Ruby, Flutter, Swift/iOS, Android, Docker, Terraform, embedded, or specialized environments, use the exact wrapper/toolchain and runner required by that project. Optional workflow examples are in `templates/workflows/`. Example workflow files deliberately contain SHA placeholders and are not active until verified and configured.

**No checks configured:** by default, CI warns and exits successfully after validating the profile. It explicitly reports that application code was not built or tested. Set `ci.noChecksPolicy` to `fail` when the project requires at least one configured command before merge.

## 5. AI agent configuration

- GitHub Copilot repository-wide instructions: `.github/copilot-instructions.md`.
- Path-specific Copilot instructions: `.github/instructions/*.instructions.md`.
- Copilot specialist agents: `.github/agents/*.agent.md`.
- Reusable prompts: `.github/prompts/*.prompt.md`.
- Other assistants may require their own root instruction file or explicit context; this kit does not claim that every editor/agent automatically reads GitHub Copilot files.

The agents and skills provide role guidance, not independent credentials or production authority. Their frontmatter does not restrict tools by default; inspect the available tool names in your specific Copilot/IDE host and add a least-privilege `tools` list before using them in a restricted environment. Assign only the permissions and context required for a task.

## 6. Security and enterprise governance

Read `SECURITY-HARDENING-GUIDE.md` before enabling production workflows. Repository YAML cannot switch on GitHub plan features, branch protection, required reviewers, secret scanning, code security entitlements, cloud identity, or compliance programs. Authorized owners must configure and verify those controls in the target accounts.

The active workflows use read-only `GITHUB_TOKEN` permissions and a pinned checkout action. Third-party actions in optional examples must be pinned to verified full-length commit SHAs before activation. See [GitHub's secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## 7. Optional project profiles

Keep domain-, company-, cloud-, or application-specific instructions under `profiles/` so the distributable core remains generic. Profile workflows stored there are inactive; review their triggers, permissions, secrets, branch/environment assumptions, artifact identity, rollback, and approvals before copying any file into `.github/workflows/`. Do not ship private or customer-specific configuration in a commercial package.

## 8. Validation commands

Requirements for local validation: Python 3.9+ and Ruby 3.x. The GitHub-hosted `ubuntu-latest` workflow runner includes the runtimes used by the active workflows.

From the repository root:

```bash
python3 .github/scripts/run_project_checks.py
ruby .github/scripts/validate_github_config.rb
```

The first command validates and runs only the checks defined in `project-profile.json`. The second checks YAML syntax, JSON profile validity, Copilot frontmatter, and safety rules for active workflows. Neither command is a full semantic validator for every possible GitHub Actions feature.

## 9. Commercial use and distribution

This package includes a product description, commercial-launch checklist, validation report, and commercial-license starting template in `licensing/`. Before selling or redistributing it, choose and obtain legal review of the actual license, confirm rights to every included asset and brand, define update/support obligations, and avoid marketing claims of guaranteed security, compliance, uptime, or scale. The template is not legal advice and does not grant rights by itself.

## 10. Version and change policy

See `KIT-METADATA.json`, `CHANGELOG.md`, and `KIT-VALIDATION-REPORT.md`. Before commercial distribution, complete `PRODUCT-LAUNCH-CHECKLIST.md`. Maintain semantic versioning, test changes in a sample repository for each marketed stack, verify upstream action pins, and document breaking changes before selling updates.
