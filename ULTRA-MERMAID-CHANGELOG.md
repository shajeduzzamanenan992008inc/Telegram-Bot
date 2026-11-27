# Ultra Mermaid visual upgrade — local deliverable

## Files included

- `README.md` — based on the user-uploaded README.md (not the README currently on the GitHub `main` branch); all four flowcharts upgraded and all user-facing README text converted to English.
- `docs/AI_PROVIDER_ARCHITECTURE.md` — based on `main` at commit `e9dfb8a6286857b905486a69dc58b88ee526e9f8`; its provider-routing flowchart upgraded.

## Language pass

- Translated the Bengali summary and creator/footer captions into English.
- Removed the duplicated Bengali tagline.
- Preserved factual product behavior: the README still states in English that Bangla is the bot’s default response language and that the bot supports multiple languages.
- Confirmed that no Bengali-script characters remain in the delivered Markdown files.

## Visual system

- Dark navy canvas with high-contrast cyan, blue, violet, green, amber and rose node/edge colors.
- Blue/cyan animation for the primary request and architecture paths.
- Amber animation for retry/failover and safety-gated transitions.
- Green animation for successful completion and storage flows.
- Violet styling for specialist/external integrations and operator handoffs.
- Rejection/negative/terminal branches and explicit responsibility/off-host paths stay static.
- The sequence diagram in README.md was deliberately left unchanged.

## Logic and structure

No node labels, decision labels, or existing relationships were removed or rewritten in the README flowcharts. The flowchart topology is retained; edge IDs, styling classes and animation declarations were added. Only Mermaid fenced blocks were modified.

## Rendering compatibility

Animated Mermaid edges use edge IDs and `@{ animation: fast|slow }` metadata documented by Mermaid's current flowchart syntax. Edge-ID animation requires a Mermaid renderer that supports the edge animation feature (Mermaid 11.5.0 or newer). The local environment did not have Mermaid CLI installed, so no native SVG/browser render was claimed or fabricated.

## Git status

This package is local-only. No commit, push, or new pull request is part of this deliverable. The previously opened PR #22 was closed without merging after the no-PR instruction.
