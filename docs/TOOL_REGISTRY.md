# Mission tool registry

Mission Control currently exposes **no external tools** to the model. Its only operation is an internal provider-router call that returns text analysis. The model response is stored as unverified output and cannot be treated as an action receipt.

Explicitly unavailable in missions: arbitrary shell/Python, unrestricted filesystem access, code edits, test execution, network research, service restart, device control, deletion, purchases, and production deployment. Existing bot commands for web, documents, code testing, Home Assistant, and VM control remain separately authorized and are not callable from a mission.

Adding a tool requires a fixed name, typed input/output validation, owner/admin policy, timeout/resource cap, side-effect classification, audit entry, idempotency strategy, and negative tests. Do not pass model-generated command strings to a shell.
