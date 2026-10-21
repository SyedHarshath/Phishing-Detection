# MLOps architecture and current boundaries

```mermaid
flowchart LR
  W[Website navigation] --> E[Browser extension]
  E --> A[FastAPI /predict]
  A --> P[Shared tokenizer and CNN-BiLSTM]
  P --> L[(Configured DB: PostgreSQL in production, SQLite locally)]
  E --> F[User feedback]
  F --> V[Reviewer CLI: verify / reject / dispute]
  V --> B[Approved immutable batch]
  B --> Q[Strict dataset validation CLI]
  Q --> C[Isolated smoke candidate trainer]
  C --> R[Candidate evaluation]
  R --> G[Promotion gate: explicit and disabled by default]
  G --> S[Versioned serving bundle: process restart required]
```

```mermaid
sequenceDiagram
  participant W as Website
  participant X as Extension
  participant API as FastAPI
  participant DB as Configured Database
  participant Analyst as Reviewer
  participant CLI as Offline training tools
  W->>X: HTTP(S) tab update
  X->>API: POST /predict URL
  API->>API: tokenize and classify
  API->>DB: append prediction and return event id
  API-->>X: class, score, model version
  X->>API: optional /feedback report
  API->>DB: record user claim (unverified)
  Analyst->>DB: reviewer command records decision and provenance
  CLI->>CLI: export approved labels to immutable checksummed batch
  CLI->>DB: eligibility review and persistent manual job (no training)
  CLI-->>Analyst: preview, eligibility reasons, and batch reference
```

The FastAPI service imports shared inference and the event database; production uses PostgreSQL from `DATABASE_URL`, while local development and tests retain SQLite. Startup performs additive schema initialization and never copies or deletes old records. The API and feedback/retraining lifecycle share a small connection adapter; SQLite-only overlap/evaluation workspaces and the explicitly selected offline SQLite disagreement extractor remain local tools. A scheduled review only creates a persistent manual-worker-required job and never starts training. The versioned active manifest and registry are implemented, but promotion remains gated on evidence and disabled by default. Existing serving processes do not support live model reload. Use a controlled process restart after any separately approved artifact change. See the operational readiness runbook for service and recovery procedures.
# Promotion boundary

Candidate promotion and rollback use `mlops.promotion.registry`: immutable version directories, `registry.json` history, and an atomically replaced `active.json`. The active pointer is resolved by FastAPI and Streamlit through `PhishingPredictor` at process initialization. Both processes retain the loaded model in memory and require restart after a switch; no live hot reload is implemented. Promotion remains operator-only and disabled by default. See [MODEL_PROMOTION_ROLLBACK.md](MODEL_PROMOTION_ROLLBACK.md) for the gates, commands, recovery, and unresolved data prerequisites.
