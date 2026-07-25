# DataAcrossBorders — Design Analysis & State-of-the-Art Review

A critical audit of how the solution addresses the three challenge problems, benchmarked
against the actual standards used in federated health-data systems, and the upgrades made
as a result (v2.0).

## The three problems and how each is solved

### 1. Knowing where data exists (discovery)

**Standard of reference:** the GA4GH **Beacon v2** protocol — the adopted standard for
federated biomedical data discovery. A Beacon answers *existence and aggregate counts*
across institutions, never record-level data, and delegates record access to a separate,
authorized tier.

**What we do:** `/api/discover` returns a Beacon-style response — an `exists` boolean plus
per-hospital and per-age-band counts, with `granularity: "count"`. No records ever cross
this tier.

**The weakness we found and fixed:** naive count suppression (hide any cell < k) is
vulnerable to a **query-differencing attack** — the documented failure mode of aggregate
query systems. An attacker runs two overlapping queries and subtracts the results to isolate
an individual, even when each count is ≥ k. Fixes applied:
- **Complementary cell suppression:** if suppressing small cells leaves exactly one hidden
  cell in a dimension, we also hide the next-smallest cell, so the hidden value can't be
  recovered as `total − visible`.
- **Differencing-aware auditing:** successive discover queries from the same user that differ
  by ≤ 2 filters are flagged `possible-differencing` in the audit log for the administrator.

### 2. Data privacy

**Standard of reference:** HIPAA **Safe Harbor** (remove all 18 identifier categories,
aggregate ages > 89) and the **honest-broker / pseudonymization-at-rest** pattern.

**What we do:**
- **Physical vault split.** Direct identifiers live in `vault.db`; de-identified studies in
  `clinical.db`. The link is an **HMAC-SHA256 pseudonym** (keyed with a secret, so it can't be
  reversed or rebuilt without the key). Researcher request paths never open the vault.
- **Role-based minimum-necessary disclosure.** researcher → pseudonym only; clinician → name
  + birth *year*; admin → full. Enforced server-side in the JWT, not by the UI.
- **Safe Harbor upgrades (v2):** free-text `diagnosis` is **scrubbed** for the researcher tier
  (names, dates, MRNs, UIDs, phones, emails), and ages > 89 are aggregated to `90+`. CSV export
  applies the identical redaction (an unredacted export is the classic leak).
- **Full audit trail** of every query (who, what, when, result count, flags).

Cross-hospital linkage is deliberately *not* possible (the pseudonym includes the hospital),
so the same person at two hospitals gets two keys — minimizing re-identification risk.

### 3. Indexing

**Standard of reference:** clinical search engines use an inverted index (BM25) **plus query
expansion over a controlled vocabulary** (UMLS/SNOMED CT), because keyword search alone can't
know "heart attack" = "myocardial infarction" = "MI". UMLS synonym expansion recovers ~45%
more relevant results in the literature.

**What we do:**
- **FTS5 inverted index** with Porter stemming (so "infarction" matches "infarct").
- **Medical query expansion (v2):** a curated, bidirectional concept map (`synonyms.py`)
  covering the neuro / cardiac / fetal domains present in the data. A search for "heart attack"
  is expanded to the full myocardial-infarction concept group — verified: both queries return
  the identical 83 results. Expanded terms are surfaced to the user for transparency.
- **BM25 relevance ranking** so the best matches sort first, with structured filters
  (normalized age, sex, modality, body part, hospital, date) as index-backed predicates.

## Data integrity (cross-cutting)

Added in v2, because "the data is correct and untampered" underpins all three problems:
- **Validation on ingest** — every incoming record is checked against a Pydantic contract
  (date formats, sex, age units); malformed rows are rejected, not silently loaded.
- **Referential integrity** — every study's `patient_key` must exist in the vault; the ETL
  aborts on any orphan.
- **Tamper/corruption detection** — each row carries a SHA-256 `row_hash`; a dataset-wide
  digest is stored in a manifest. `/api/verify` recomputes and compares them.
- **FTS consistency** — index row count must equal the base-table count.
- **Idempotent rebuilds** — the ETL fully rebuilds both databases deterministically.

`/api/verify` (admin-only) runs all five checks live and returns `PASS`/`FAIL`.

## What we deliberately did *not* build (and why)

- **Semantic/vector search (embeddings).** Higher recall, but adds a heavy model dependency
  and non-determinism for a 2,700-record corpus where curated synonym expansion already
  captures the concept space. Documented as the next step if the corpus grows.
- **Differential privacy noise on counts.** Stronger than k-anonymity, but the exact-count
  utility matters for a cohort-feasibility demo; complementary suppression closes the
  practical differencing hole without degrading every answer.
- **Homomorphic-encryption / on-chain beacon.** Research-grade; out of scope for the event.

## References

- GA4GH Beacon v2 — federated discovery: https://www.ga4gh.org/product/beacon-api/
- Query-differencing / aggregate-query side channels: https://people.mpi-sws.org/~francis/side-channel.pdf
- UMLS synonym expansion for clinical search: https://link.springer.com/article/10.1186/1472-6947-12-12
- HIPAA Safe Harbor method: https://www.getlimina.ai/en/blog/hipaa-safe-harbor-method-guide
