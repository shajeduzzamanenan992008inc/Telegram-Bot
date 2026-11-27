# Mission tool registry

Mission Control currently exposes **no external tools** to the model. Its only operation is an internal provider-router call that returns text analysis. The model response is stored as unverified output and cannot be treated as an action receipt.

Explicitly unavailable in missions: arbitrary shell/Python, unrestricted filesystem access, code edits, test execution, network research, service restart, device control, deletion, purchases, and production deployment. Existing bot commands for web, documents, code testing, Home Assistant, and VM control remain separately authorized and are not callable from a mission.

Adding a tool requires a fixed name, typed input/output validation, owner/admin policy, timeout/resource cap, side-effect classification, audit entry, idempotency strategy, and negative tests. Do not pass model-generated command strings to a shell.

## Action result contract

Actions executed through `ActionRegistry` return a structured result with the tool name, unique operation ID, elapsed milliseconds, safe diagnostic code, and optional verification evidence. The status is one of:

- `done`: the operation completed with direct evidence, such as Telegram returning a sent message or an isolated test process exiting successfully.
- `unverified`: a request or side effect was accepted, but its intended external state was not independently checked (for example, creating an admin approval or calling an allowlisted Home Assistant service).
- `failed`: validation, permission, idempotency, or execution failed. The result contains a safe diagnostic code rather than raw exception text.

The assistant receives this structured contract as the tool result. It must not describe `unverified` as completed or claim a `failed` operation succeeded. Operation records retain only the bounded structured result; raw tool arguments and provider prompts are not included.
