# Self-learning signals

This bot does not train model weights. Its improvement loop uses explicit `/feedback` ratings, existing provider reliability telemetry, and administrator review. Feedback stores only a rating and a controlled category (`accuracy`, `language`, `code`, `provider`, `latency`, `format`, `tool`, `privacy`, or `other`); it does not copy the rated conversation into the learning table.

`/learning_status` shows repeated negative categories and open proposals. `/learning_pause` stops new feedback intake, automatic fact extraction, and proposal collection; `/learning_resume` enables them again. This global switch does not change a user's separately consented `/memory` preference or delete/restrict retrieval of existing facts. Explicit user `/remember` remains available.

The existing `/patch` command can generate a proposed unified diff from a project ZIP. In a private chat, the exact patch and source ZIP are encrypted with `APP_DATA_KEY` and retained for up to seven days while the proposal is active. Passing a feedback category or receiving an AI suggestion does not modify prompts, configuration, source code, or model weights.

Mission plans and results are separately encrypted. They are not mixed into user memories or the feedback signal table. `/privacy export` includes owned missions and proposal metadata/diffs; `/privacy delete confirm` cancels missions and deletes owned mission/proposal records.

## Limits

- Feedback is a review signal, not a verified fact or training dataset.
- An unknown feedback category is stored as `other`.
- No automatic prompt tuning, model fine-tuning, or silent configuration changes are implemented.
- Metrics and provider status do not identify private conversation content.
