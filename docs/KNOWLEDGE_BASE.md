# Personal Knowledge Base

The Personal KB is an optional, explicit-consent store, separate from conversation history and short-lived uploaded-file indexes. It requires server-side `PERSONAL_KB_ENABLED=true` and user `/kb on`. After uploading a supported document, use `/files` to find its temporary index ID and `/kb save ID` to promote it into the encrypted, user-scoped KB.

Commands:

```text
/kb on|off
/kb save <temporary-file-ID>
/kb search <query>
/kb list
/kb delete <ID|all>
```

The database enforces per-user document and encrypted-byte limits. Retrieved passages are included as untrusted reference data, not instructions; source filenames/locations are retained for citation in responses. `/kb off` disables retrieval but does not delete saved items. `/kb delete all` removes them. `/privacy delete confirm` also removes user-linked KB content. Encryption/backup key requirements are described in [configuration](./CONFIGURATION.md) and the [README privacy section](../README.md#defense-in-depth).

Retrieval uses the repository's bounded local text-matching path; it is not an embedding/vector index and does not guarantee semantic recall or factual verification.
