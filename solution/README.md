# DataAcrossBorders — Solution Layer

The federated aggregation, privacy, and access-control layer built on top of the three
(unmodified) hospital boilerplate nodes.

## How the three problems are solved

| Problem | Solution |
| --- | --- |
| **Knowing where data exists** | Tier-1 `/api/discover`: Beacon-style `exists` + per-hospital and age-band counts, never records. k-anonymity floor **with complementary cell suppression** (safe against query differencing) + differencing-aware auditing. |
| **Data privacy** | PII lives in a **physically separate database** (`vault.db`), linked only by an HMAC pseudonym (`patient_key`). Researcher requests never open the vault, get pseudonyms, and their free-text diagnosis is **scrubbed to HIPAA Safe Harbor**; ages > 89 aggregated to `90+`. Clinicians get name + birth *year*; admins get everything. CSV export applies the same redaction. Every query is audit-logged. |
| **Indexing** | ETL normalizes mixed-unit DICOM ages → float years into SQLite with B-tree indexes + an **FTS5 inverted index** (Porter stemming). Search adds **UMLS/SNOMED-style medical query expansion** ("heart attack" → "myocardial infarction") and **BM25 relevance ranking**. |
| **Data integrity** | Pydantic validation on ingest, per-row SHA-256 hashes, a dataset digest manifest, referential-integrity + FTS-consistency checks. `/api/verify` (admin) runs the self-test live. |

See **[ANALYSIS.md](ANALYSIS.md)** for the full state-of-the-art review and design rationale.

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

## Tests

```bash
pytest -q        # from solution/, after etl.py has built the databases
```

25 tests lock in the guarantees: data integrity, the three privacy tiers, CSV
redaction, k-anonymity + differencing safety, synonym expansion, Safe Harbor
scrubbing, access control, and malformed-input robustness.

## Why SQLite and not a hosted MySQL?

Zero credentials, zero network dependency, ships in Python's stdlib, and FTS5 gives a real
inverted index. The two-file split makes the privacy boundary *physical* rather than a
GRANT policy. The schema (`schema_*.sql`) is deliberately MySQL-portable if a server DB
is ever required.
