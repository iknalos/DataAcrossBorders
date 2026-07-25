"""DataAcrossBorders aggregator — the federated search / privacy / access-control layer.

Two-tier search over the three hospital nodes' data (loaded by etl.py):

  /api/discover  Tier 1: existence + counts only, k-anonymity floor (any role)
  /api/search    Tier 2: record-level results, redacted per role

Privacy boundary is physical: PII lives in vault.db, and only clinician/admin
request paths ever open it. Researcher responses carry pseudonyms only.

Run:  uvicorn app:app --port 8000 --reload
"""

import csv
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

BASE = Path(__file__).parent
K_ANONYMITY_FLOOR = 5
AGE_BANDS = [(0, 1), (1, 5), (5, 12), (12, 18), (18, 40), (40, 65), (65, 200)]

app = FastAPI(
    title="DataAcrossBorders Aggregator",
    description="Federated medical imaging search with a physical PII vault and role-based redaction.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def clinical_db() -> sqlite3.Connection:
    conn = sqlite3.connect(BASE / "clinical.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def vault_db() -> sqlite3.Connection:
    # Only ever called on clinician/admin paths — this is the privacy boundary.
    conn = sqlite3.connect(BASE / "vault.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def audit(user: dict, endpoint: str, params: dict, result_count: int) -> None:
    conn = clinical_db()
    with conn:
        conn.execute(
            "INSERT INTO audit_log (ts_utc, username, role, endpoint, params_json, result_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                user["username"],
                user["role"],
                endpoint,
                json.dumps({k: v for k, v in params.items() if v is not None}),
                result_count,
            ),
        )
    conn.close()


def display_name(pn: str) -> str:
    """DICOM PN 'Last^First^Middle' -> 'Last, First Middle' for display.
    The vault keeps the raw DICOM value; formatting is presentation-only."""
    parts = [p for p in pn.split("^") if p]
    return parts[0] if len(parts) <= 1 else f"{parts[0]}, {' '.join(parts[1:])}"


def display_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}" if len(yyyymmdd) == 8 else yyyymmdd


def build_where(q, age_min, age_max, sex, modality, body_part, hospital):
    """Returns (sql_fragment, params). FTS terms are quoted to disable operators."""
    where, params = [], []
    if q:
        fts = " ".join(f'"{t}"' for t in q.replace('"', " ").split())
        where.append(
            "s.study_key IN (SELECT rowid FROM studies_fts WHERE studies_fts MATCH ?)"
        )
        params.append(fts)
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
    return (" WHERE " + " AND ".join(where)) if where else "", params


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
    """Tier 1 — cohort discovery. Returns only WHERE the data exists and HOW MUCH,
    never records. Counts below the k-anonymity floor are suppressed."""
    where, params = build_where(q, age_min, age_max, sex, modality, body_part, hospital)
    conn = clinical_db()

    def k_suppress(n: int):
        return n if n == 0 or n >= K_ANONYMITY_FLOOR else f"<{K_ANONYMITY_FLOOR}"

    per_hospital = {
        r["hospital"]: k_suppress(r["n"])
        for r in conn.execute(
            f"SELECT s.hospital, COUNT(*) AS n FROM studies s{where} GROUP BY s.hospital", params
        )
    }
    total = conn.execute(f"SELECT COUNT(*) FROM studies s{where}", params).fetchone()[0]
    bands = {}
    for lo, hi in AGE_BANDS:
        n = conn.execute(
            f"SELECT COUNT(*) FROM studies s{where}"
            + (" AND " if where else " WHERE ")
            + "s.age_years >= ? AND s.age_years < ?",
            params + [lo, hi],
        ).fetchone()[0]
        bands[f"{lo}-{hi if hi < 200 else '+'}"] = k_suppress(n)
    conn.close()

    audit(user, "/api/discover", dict(q=q, age_min=age_min, age_max=age_max, sex=sex,
                                      modality=modality, body_part=body_part, hospital=hospital), total)
    return {
        "tier": "discovery",
        "k_anonymity_floor": K_ANONYMITY_FLOOR,
        "total_matches": k_suppress(total),
        "by_hospital": per_hospital,
        "by_age_band": bands,
    }


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
    """Tier 2 — record-level search. PII exposure depends on role:
    researcher: pseudonym only | clinician: name + birth YEAR | admin: everything.
    format=csv streams the same redacted result set as a download."""
    role = user["role"]
    where, params = build_where(q, age_min, age_max, sex, modality, body_part, hospital)
    conn = clinical_db()
    total = conn.execute(f"SELECT COUNT(*) FROM studies s{where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT s.* FROM studies s{where} ORDER BY s.hospital, s.study_id LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()

    results = [
        {
            "hospital": r["hospital"],
            "study_id": r["study_id"],
            "patient": {"pseudonym": r["patient_key"]},
            "age_years": r["age_years"],
            "sex": r["sex"],
            "study_date": display_date(r["study_date"]),
            "modality": r["modality"],
            "body_part": r["body_part"],
            "diagnosis": r["diagnosis"],
        }
        for r in rows
    ]

    if role in ("clinician", "admin") and results:
        # Re-identification: the ONLY place vault.db is opened.
        keys = [r["patient"]["pseudonym"] for r in results]
        vconn = vault_db()
        ph = ",".join("?" * len(keys))
        pii = {
            r["patient_key"]: r
            for r in vconn.execute(f"SELECT * FROM patients WHERE patient_key IN ({ph})", keys)
        }
        vconn.close()
        for res in results:
            p = pii.get(res["patient"]["pseudonym"])
            if p is None:
                continue
            if role == "clinician":
                res["patient"] = {
                    "name": display_name(p["patient_name"]),
                    "patient_id": p["patient_id"],
                    "birth_year": p["birth_date"][:4],
                }
            else:  # admin
                res["patient"] = {
                    "name": display_name(p["patient_name"]),
                    "patient_id": p["patient_id"],
                    "birth_date": display_date(p["birth_date"]),
                }

    audit(user, f"/api/search ({format})", dict(q=q, age_min=age_min, age_max=age_max, sex=sex, modality=modality,
                                                body_part=body_part, hospital=hospital, limit=limit, offset=offset), len(results))

    if format == "csv":
        # Same redacted rows as the JSON path — export must never widen exposure.
        pii_cols = {"researcher": ["pseudonym"],
                    "clinician": ["name", "patient_id", "birth_year"],
                    "admin": ["name", "patient_id", "birth_date"]}[role]
        cols = ["hospital", "study_id", *pii_cols, "age_years", "sex",
                "study_date", "modality", "body_part", "diagnosis"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for res in results:
            writer.writerow({**{k: v for k, v in res.items() if k != "patient"}, **res["patient"]})
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="dab_export.csv"'},
        )

    return {"tier": "records", "role": role, "total_matches": total,
            "returned": len(results), "offset": offset, "results": results}


@app.get("/api/audit")
def audit_view(user: dict = Depends(current_user), limit: int = Query(default=50, le=500)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Audit log is admin-only.")
    conn = clinical_db()
    rows = conn.execute(
        "SELECT ts_utc, username, role, endpoint, params_json, result_count "
        "FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"entries": [dict(r) for r in rows]}
