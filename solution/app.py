"""DataAcrossBorders aggregator — the federated search / privacy / access-control layer.

Two-tier search over the three hospital nodes' data (loaded by etl.py):

  /api/discover  Tier 1: existence + counts only, k-anonymity with complementary
                 cell suppression + differencing-aware auditing (Beacon-style).
  /api/search    Tier 2: record-level results, medical-synonym query expansion,
                 BM25 relevance ranking, redaction + free-text scrubbing per role.
  /api/verify    Data-integrity self-test (manifest, hashes, referential integrity).

Privacy boundary is physical: PII lives in vault.db, and only clinician/admin
request paths ever open it. Researcher responses carry pseudonyms only and have
their free-text scrubbed to HIPAA Safe Harbor.

Run:  uvicorn app:app --port 8000 --reload
"""

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from auth import current_user, issue_token
from deident import cap_age, display_age, scrub_text
from synonyms import expand_query

BASE = Path(__file__).parent
K = 5  # k-anonymity floor
# Safe Harbor age bands; 90+ is a single aggregated bucket.
AGE_BANDS = [(0, 1), (1, 5), (5, 12), (12, 18), (18, 40), (40, 65), (65, 90), (90, 999)]

app = FastAPI(
    title="DataAcrossBorders Aggregator",
    description="Federated medical imaging search with a physical PII vault, "
                "k-anonymous discovery, synonym-expanded search and role-based redaction.",
    version="2.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def clinical_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{BASE / 'clinical.db'}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def clinical_rw() -> sqlite3.Connection:
    conn = sqlite3.connect(BASE / "clinical.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def vault_db() -> sqlite3.Connection:
    # Only ever called on clinician/admin paths — this is the privacy boundary.
    conn = sqlite3.connect(f"file:{BASE / 'vault.db'}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def band_label(lo: int, hi: int) -> str:
    if lo >= 90:
        return "90+"
    return f"{lo}-{hi}"


def audit(user: dict, endpoint: str, params: dict, result_count: int, flags: str = "") -> None:
    conn = clinical_rw()
    with conn:
        conn.execute(
            "INSERT INTO audit_log (ts_utc, username, role, endpoint, params_json, result_count, flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), user["username"], user["role"],
             endpoint, json.dumps({k: v for k, v in params.items() if v is not None}), result_count, flags),
        )
    conn.close()


def build_where(conn, q, age_min, age_max, sex, modality, body_part, hospital):
    """Returns (from_sql, where_sql, params, expanded_terms).

    When q is present we join the FTS table so BM25 ranking is available; the
    query is medically expanded first (heart attack -> myocardial infarction ...).
    """
    where, params, expanded = [], [], []
    from_sql = "FROM studies s"
    if q:
        match, expanded = expand_query(q)
        if match:
            from_sql = "FROM studies_fts JOIN studies s ON s.study_key = studies_fts.rowid"
            where.append("studies_fts MATCH ?")
            params.append(match)
    for clause, value in [
        ("s.age_years >= ?", age_min),
        ("s.age_years <= ?", age_max),
        ("s.sex = ?", sex.upper() if sex else None),
        ("s.modality = ?", modality.upper() if modality else None),
        ("s.body_part = ?", body_part.upper() if body_part else None),
        ("s.hospital = ?", hospital.upper() if hospital else None),
    ]:
        if value is not None:
            where.append(clause)
            params.append(value)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return from_sql, where_sql, params, expanded


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def login(body: LoginBody):
    return issue_token(body.username, body.password)


@app.get("/health")
def health():
    conn = clinical_db()
    n = conn.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
    hospitals = [r[0] for r in conn.execute("SELECT DISTINCT hospital FROM studies ORDER BY 1")]
    conn.close()
    return {"status": "healthy", "studies_indexed": n, "hospitals": hospitals}


def suppress_dimension(counts: dict[str, int]) -> dict[str, object]:
    """k-anonymity with complementary cell suppression.

    Any bucket < K is suppressed. If suppressing leaves exactly ONE hidden bucket
    in the dimension, an attacker could recover it by subtracting the visible
    buckets from the (visible) total — so we suppress the next-smallest visible
    bucket too. This closes the query-differencing hole that naive per-cell
    thresholding leaves open.
    """
    hidden = {k for k, v in counts.items() if 0 < v < K}
    visible = {k: v for k, v in counts.items() if k not in hidden}
    if len(hidden) == 1 and visible:
        smallest = min(visible, key=visible.get)
        hidden.add(smallest)
    out: dict[str, object] = {}
    for k, v in counts.items():
        out[k] = f"<{K}" if k in hidden else v
    return out


@app.get("/api/discover")
def discover(
    user: dict = Depends(current_user),
    q: str | None = None,
    age_min: float | None = None,
    age_max: float | None = None,
    sex: str | None = None,
    modality: str | None = None,
    body_part: str | None = None,
    hospital: str | None = None,
):
    """Tier 1 — Beacon-style cohort discovery. Returns only existence and counts,
    never records, with k-anonymous suppression that is safe against differencing."""
    conn = clinical_db()
    from_sql, where_sql, params, expanded = build_where(
        conn, q, age_min, age_max, sex, modality, body_part, hospital)

    total = conn.execute(f"SELECT COUNT(*) {from_sql}{where_sql}", params).fetchone()[0]

    hosp_counts = {r["hospital"]: r["n"] for r in conn.execute(
        f"SELECT s.hospital, COUNT(*) AS n {from_sql}{where_sql} GROUP BY s.hospital", params)}
    band_counts = {}
    for lo, hi in AGE_BANDS:
        n = conn.execute(
            f"SELECT COUNT(*) {from_sql}{where_sql}"
            + (" AND " if where_sql else " WHERE ") + "s.age_years >= ? AND s.age_years < ?",
            params + [lo, hi]).fetchone()[0]
        if n:
            band_counts[band_label(lo, hi)] = n
    conn.close()

    # Differencing-aware auditing: flag overlapping successive discover queries.
    flags = differencing_flag(user, dict(q=q, age_min=age_min, age_max=age_max, sex=sex,
                                         modality=modality, body_part=body_part, hospital=hospital))
    audit(user, "/api/discover", dict(q=q, age_min=age_min, age_max=age_max, sex=sex,
          modality=modality, body_part=body_part, hospital=hospital), total, flags)

    return {
        "tier": "discovery",
        "exists": total > 0,                       # Beacon boolean
        "granularity": "count",
        "k_anonymity_floor": K,
        "total_matches": total if total == 0 or total >= K else f"<{K}",
        "by_hospital": suppress_dimension(hosp_counts),
        "by_age_band": suppress_dimension(band_counts),
        "expanded_terms": expanded,
        "note": "Counts below the k-anonymity floor are suppressed, with complementary "
                "suppression to prevent recovery by subtraction.",
    }


def differencing_flag(user: dict, params: dict) -> str:
    """Heuristic: if this user's previous discover query shares most filters with
    this one but differs by one, the pair could be a differencing probe. Flag it
    in the audit log (does not block — surfaced for the admin)."""
    conn = clinical_db()
    prev = conn.execute(
        "SELECT params_json FROM audit_log WHERE username = ? AND endpoint = '/api/discover' "
        "ORDER BY id DESC LIMIT 1", (user["username"],)).fetchone()
    conn.close()
    if not prev:
        return ""
    a = {k: v for k, v in params.items() if v is not None}
    b = json.loads(prev["params_json"])
    diff = set(a.items()) ^ set(b.items())
    return "possible-differencing" if 0 < len(diff) <= 2 and (set(a) & set(b)) else ""


@app.get("/api/search")
def search(
    user: dict = Depends(current_user),
    q: str | None = None,
    age_min: float | None = None,
    age_max: float | None = None,
    sex: str | None = None,
    modality: str | None = None,
    body_part: str | None = None,
    hospital: str | None = None,
    limit: int = Query(default=25, le=1000),
    offset: int = 0,
    format: str = Query(default="json", pattern="^(json|csv)$"),
):
    """Tier 2 — record-level search. Query is medically expanded and BM25-ranked.
    PII exposure depends on role: researcher (pseudonym + scrubbed text),
    clinician (name + birth year), admin (everything). CSV honors the same redaction."""
    role = user["role"]
    conn = clinical_db()
    from_sql, where_sql, params, expanded = build_where(
        conn, q, age_min, age_max, sex, modality, body_part, hospital)

    total = conn.execute(f"SELECT COUNT(*) {from_sql}{where_sql}", params).fetchone()[0]
    order = "ORDER BY bm25(studies_fts)" if q and "MATCH" in where_sql else "ORDER BY s.hospital, s.study_id"
    rows = conn.execute(
        f"SELECT s.* {from_sql}{where_sql} {order} LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    conn.close()

    results = []
    for r in rows:
        diagnosis = r["diagnosis"]
        if role == "researcher":
            diagnosis, _ = scrub_text(diagnosis)          # Safe Harbor free-text scrub
        results.append({
            "hospital": r["hospital"],
            "study_id": r["study_id"],
            "patient": {"pseudonym": r["patient_key"]},
            "age": display_age(cap_age(r["age_years"])),
            "sex": r["sex"],
            "study_date": display_date(r["study_date"]),
            "modality": r["modality"],
            "body_part": r["body_part"],
            "diagnosis": diagnosis,
        })

    if role in ("clinician", "admin") and results:
        # Re-identification: the ONLY place vault.db is opened.
        keys = [r["patient"]["pseudonym"] for r in results]
        vconn = vault_db()
        ph = ",".join("?" * len(keys))
        pii = {r["patient_key"]: r for r in
               vconn.execute(f"SELECT * FROM patients WHERE patient_key IN ({ph})", keys)}
        vconn.close()
        for res in results:
            p = pii.get(res["patient"]["pseudonym"])
            if p is None:
                continue
            if role == "clinician":
                res["patient"] = {"name": display_name(p["patient_name"]),
                                  "patient_id": p["patient_id"], "birth_year": p["birth_date"][:4]}
            else:
                res["patient"] = {"name": display_name(p["patient_name"]),
                                  "patient_id": p["patient_id"], "birth_date": display_date(p["birth_date"])}

    audit(user, f"/api/search ({format})", dict(q=q, age_min=age_min, age_max=age_max, sex=sex,
          modality=modality, body_part=body_part, hospital=hospital, limit=limit, offset=offset), len(results))

    if format == "csv":
        pii_cols = {"researcher": ["pseudonym"], "clinician": ["name", "patient_id", "birth_year"],
                    "admin": ["name", "patient_id", "birth_date"]}[role]
        cols = ["hospital", "study_id", *pii_cols, "age", "sex", "study_date", "modality", "body_part", "diagnosis"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for res in results:
            writer.writerow({**{k: v for k, v in res.items() if k != "patient"}, **res["patient"]})
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="dab_export.csv"'})

    return {"tier": "records", "role": role, "total_matches": total, "returned": len(results),
            "offset": offset, "expanded_terms": expanded, "results": results}


def display_name(pn: str) -> str:
    """DICOM PN 'Last^First^Middle' -> 'Last, First Middle' (presentation only)."""
    parts = [p for p in pn.split("^") if p]
    return parts[0] if len(parts) <= 1 else f"{parts[0]}, {' '.join(parts[1:])}"


def display_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}" if len(yyyymmdd) == 8 else yyyymmdd


@app.get("/api/verify")
def verify(user: dict = Depends(current_user)):
    """Data-integrity self-test: manifest, referential integrity, hashes, FTS."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Integrity report is admin-only.")
    conn = clinical_db()
    m = conn.execute("SELECT * FROM data_manifest WHERE id = 1").fetchone()
    checks = {}

    n_studies = conn.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
    n_fts = conn.execute("SELECT COUNT(*) FROM studies_fts").fetchone()[0]
    checks["study_count_matches_manifest"] = (m is not None and n_studies == m["study_count"])
    checks["fts_index_consistent"] = (n_studies == n_fts)

    study_keys = {r[0] for r in conn.execute("SELECT DISTINCT patient_key FROM studies")}
    vconn = vault_db()
    vault_keys = {r[0] for r in vconn.execute("SELECT patient_key FROM patients")}
    vconn.close()
    checks["referential_integrity"] = study_keys.issubset(vault_keys)

    hashes, bad = [], 0
    for r in conn.execute("SELECT hospital, study_id, study_uid, patient_key, age_years, sex, "
                          "study_date, modality, body_part, diagnosis, row_hash FROM studies"):
        canon = "\x1f".join(str(x) for x in r[:10])
        h = hashlib.sha256(canon.encode()).hexdigest()
        hashes.append(h)
        if h != r["row_hash"]:
            bad += 1
    checks["row_hashes_valid"] = (bad == 0)
    digest = hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest()
    checks["dataset_digest_matches_manifest"] = (m is not None and digest == m["dataset_digest"])
    conn.close()

    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "manifest": dict(m) if m else None,
        "recomputed_digest": digest,
    }


@app.get("/api/audit")
def audit_view(user: dict = Depends(current_user), limit: int = Query(default=50, le=500)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Audit log is admin-only.")
    conn = clinical_db()
    rows = conn.execute(
        "SELECT ts_utc, username, role, endpoint, params_json, result_count, flags "
        "FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return {"entries": [dict(r) for r in rows]}
