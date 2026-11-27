# Universal Enterprise Specialist Agents

All agents should follow `.github/copilot-instructions.md`. Choose only the specialist relevant to the task; the main agent owns scope, integration, and the final report.

| Agent | Use it for |
|---|---|
| `architecture-reviewer` | System design, architecture boundaries, scalability, ADRs, and trade-offs |
| `ai-safety` | AI/LLM systems, retrieval, agents, prompt injection, and tool governance |
| `api-identity` | API contracts, authentication, authorization, sessions, and integrations |
| `database-steward` | Schemas, migrations, integrity, query behavior, backup, and recovery |
| `dependency-guardian` | Dependency vulnerabilities, licensing, compatibility, and supply chain |
| `performance-reliability` | Profiling, capacity, resilience, observability, and recovery |
| `quality-engineer` | Test strategy, acceptance evidence, regression, and release quality |
| `release-guardian` | Artifact identity, rollout, deployment safety, and rollback |
| `security-architect` | Threat modeling, privacy, security design, and risk remediation |

## Coordination model
1. The main session defines the task, constraints, and approval boundary.
2. Perform read-only repository discovery before implementation.
3. Delegate one bounded review to a relevant specialist when useful; do not invoke all agents for every task.
4. The Quality Engineer identifies verification; the Security Architect reviews trust boundaries when risk warrants it.
5. The Release Guardian is involved only when packaging, deployment, or production readiness is in scope.
6. The main session reconciles findings, reviews diffs, runs available checks, and reports evidence.

Agents do not automatically possess permissions, tools, credentials, repository context, or production access. Verify each capability and grant the least privilege needed.
