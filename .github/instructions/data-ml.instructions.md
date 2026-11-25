---
applyTo: "**/*.{ipynb,parquet,arrow,sql,py,r,scala},**/notebooks/**,**/datasets/**,**/pipelines/**,**/models/**,**/training/**"
---

# Data, analytics, and machine-learning instructions

- Establish data provenance, ownership, purpose, sensitivity, schema, freshness, and retention before processing data.
- Use synthetic or approved de-identified data in development and tests; do not expose private records in notebooks, logs, or generated artifacts.
- Make pipelines idempotent, observable, restartable, and explicit about late, duplicate, missing, and invalid records.
- Track dataset, code, configuration, feature, and model versions so results can be reproduced.
- Validate data quality, leakage, skew, bias, model drift, and performance with suitable evaluation methodology.
- Separate training, validation, and test data correctly. Do not tune against the final test set.
- Document assumptions, lineage, limitations, and deployment/rollback behavior for production models.
