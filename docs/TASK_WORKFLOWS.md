# Task workflows

Start with `/mission_new <task>`. Bengali, Roman Bangla, English, and mixed-language input is forwarded to the configured model under the user's current local/cloud privacy mode. The model returns a JSON plan with at most `AGENT_MAX_STEPS`; invalid JSON falls back to one analysis step.

The workflow honors the plan's earlier-step dependencies and runs ready independent steps in batches bounded by `AGENT_MAX_WORKERS`. A dependent step receives its prerequisite notes, explicitly marked unverified. Every completed step is checkpointed separately before dependents are released. Provider routing supplies bounded key rotation, fallback, retry, and rate/cost checks.

Use `/mission_status ID` to inspect progress; `/mission_report ID` returns the final report; `/mission_pause ID` stops before the next analysis step; `/mission_resume ID` continues from the last saved step; `/mission_cancel ID` stops an active task; `/mission_delete ID` erases its task data. `/mission_list` is owner-scoped.

After a restart, incomplete work is paused and must be resumed by its owner. If a provider failed mid-step, that unfinished informational step may be repeated; completed steps are not repeated. There are no external side effects to reconcile.
