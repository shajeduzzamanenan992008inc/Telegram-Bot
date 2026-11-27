# Failure recovery

- Provider failure: the existing provider router performs its configured bounded retries and fallback; the mission becomes `failed` with a sanitized error class if the step still fails. The owner can start a new mission or resume a paused one.
- Process restart: startup changes `planning`/`running` missions to `paused`; only the owner may resume. Completed step notes remain encrypted and are not rerun.
- Mission cancellation/deletion: cancellation interrupts the current informational provider call when possible; deletion removes the owner's record. `/privacy delete confirm` also cancels active owner missions and deletes proposal records.
- Sandbox unavailable: proposal remains `testing`; no admin approval is allowed until `/evolve_test ID` records a real passing run.
- Patch application/test failure: the sandbox report is stored, proposal becomes `failed`, and approval is blocked. The production source is not touched.
- Expired proposal: its patch and source ZIP are erased. A new proposal and fresh approval are required for changed content.

There is no automatic production recovery because this bot does not deploy. Follow the manual [deployment and rollback guide](DEPLOYMENT_AND_ROLLBACK.md).
