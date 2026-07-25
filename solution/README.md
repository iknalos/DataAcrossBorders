# DataAcrossBorders — Solution Layer

The federated aggregation, privacy, and access-control layer built on top of the three
(unmodified) hospital boilerplate nodes.

## How the three problems are solved

| Problem | Solution |
| --- | --- |
| **Knowing where data exists** | Tier-1 `/api/discover`: per-hospital counts + age-band histogram. Never returns records. Counts under 5 are suppressed (k-anonymity). |
| **Data privacy** | PII lives in a **physically separate database** (`vault.db`), linked to clinical data only by an HMAC pseudonym (`patient_key`). Researcher requests never open the vault. Clinicians get name + birth *year*; admins get everything. Every query is audit-logged. |
| **Indexing** | ETL normalizes the data (mixed-unit DICOM ages → float years) into SQLite with B-tree indexes on filter columns and an **FTS5 inverted index** over diagnosis text. |

## Architecture

```
UI (docs/index.html, GitHub Pages or local)
        │ JWT
Aggregator :8000  (app.py — auth, 2-tier search, redaction, audit)
        │ reads
clinical.db (de-identified + FTS5)     vault.db (PII, role-gated)
        ▲
     etl.py  ← pulls once from the three nodes
        │
BCH :8001   MGH :8002   BWH :8003   (boilerplate, untouched)
```

## Run it

```bash
pip install -r requirements.txt

# 1. Start the three hospital nodes (three terminals, from repo root)
HOSPITAL_NODE=BCH uvicorn main:app --port 8001
HOSPITAL_NODE=MGH uvicorn main:app --port 8002
HOSPITAL_NODE=BWH uvicorn main:app --port 8003

# 2. Ingest into the two databases (from solution/)
python etl.py

# 3. Start the aggregator (from solution/)
uvicorn app:app --port 8000

# 4. Open the UI: docs/index.html (or the GitHub Pages site) — it talks to :8000
```

Demo users (password `demo123`): `dr.chen` (clinician) · `res.kim` (researcher) · `admin`.

## Why SQLite and not a hosted MySQL?

Zero credentials, zero network dependency, ships in Python's stdlib, and FTS5 gives a real
inverted index. The two-file split makes the privacy boundary *physical* rather than a
GRANT policy. The schema (`schema_*.sql`) is deliberately MySQL-portable if a server DB
is ever required.
