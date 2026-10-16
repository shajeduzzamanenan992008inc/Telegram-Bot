# Mission and evolution data privacy

- Mission requests, plans, step results, and error status are encrypted using `APP_DATA_KEY`; the database stores an opaque task fingerprint for duplicate detection. Only the owner can list/read/control their mission. Mission commands require a private Telegram chat.
- Interrupted missions are paused on service startup and resume only after `/mission_resume ID` by the owner. Mission content is erased when its 30-day expiry is processed. Terminal metadata is cleaned after 90 days from its last state update.
- `/privacy export` includes owned mission detail and evolution proposal metadata plus the proposed diff. `/privacy delete confirm` cancels active missions and deletes missions and proposals created by that account.
- Evolution proposal request summary, patch, and a repacked source ZIP (secret-named files/caches excluded) are encrypted with the same app data key. They expire after seven days. Cancellation/expiry erases their encrypted payload and test report; terminal metadata is pruned 90 days after its last state update.
- Feedback stores only a rating and fixed category. It does not store the message or answer being rated. Admin `/learning_pause` also pauses background fact extraction while leaving user memory preferences and existing memory retrieval unchanged.
- Telegram admin notices contain a proposal ID only. Admin review commands return the exact diff and bounded test report to allowlisted administrators.

Keep `APP_DATA_KEY` stable. Losing/changing it prevents decryption of retained mission/proposal payloads and encrypted backups. Restrict access to the database and backups as described in [security](SECURITY.md).
