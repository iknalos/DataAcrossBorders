"""ETL: pull all studies from the three running hospital nodes and load them into
two physically separate SQLite databases.

  vault.db     PII only (name, MRN, birth date)  -> schema_vault.sql
  clinical.db  de-identified studies + FTS5 index + audit log -> schema_clinical.sql

The split databases ARE the privacy boundary: researcher queries only ever get a
connection to clinical.db. Re-running this script is idempotent (databases are rebuilt).

Usage:  python etl.py          (nodes must be running on :8001-:8003)
"""

import hashlib
import hmac
import sqlite3
import sys
from pathlib import Path

import httpx

BASE = Path(__file__).parent
NODES = {
    "BCH": "http://localhost:8001",
    "MGH": "http://localhost:8002",
    "BWH": "http://localhost:8003",
}
# Demo secret. In production this lives in a KMS and is rotated; anyone without it
# cannot regenerate the pseudonyms even with a copy of clinical.db.
PSEUDONYM_SECRET = b"dab-demo-pseudonym-secret"


def age_to_years(age: str) -> float:
    """Normalize DICOM age strings (007Y / 011M / 005D) to float years."""
    n, unit = int(age[:-1]), age[-1].upper()
    return round({"Y": n * 1.0, "M": n / 12.0, "D": n / 365.25}[unit], 3)


def pseudonym(hospital: str, patient_id: str) -> str:
    msg = f"{hospital}|{patient_id}".encode()
    return hmac.new(PSEUDONYM_SECRET, msg, hashlib.sha256).hexdigest()[:16]


def fetch_all() -> dict[str, list[dict]]:
    out = {}
    with httpx.Client(timeout=30) as client:
        for hospital, base_url in NODES.items():
            r = client.get(f"{base_url}/api/studies")
            r.raise_for_status()
            out[hospital] = r.json()
            print(f"  {hospital}: {len(out[hospital])} studies fetched")
    return out


def main() -> None:
    print("Fetching from hospital nodes...")
    try:
        data = fetch_all()
    except httpx.ConnectError as e:
        sys.exit(f"ERROR: a hospital node is not reachable ({e}). Start all three nodes first.")

    for db in ("clinical.db", "vault.db"):
        (BASE / db).unlink(missing_ok=True)

    clinical = sqlite3.connect(BASE / "clinical.db")
    vault = sqlite3.connect(BASE / "vault.db")
    clinical.executescript((BASE / "schema_clinical.sql").read_text())
    vault.executescript((BASE / "schema_vault.sql").read_text())

    n_studies = n_patients = 0
    for hospital, records in data.items():
        for rec in records:
            key = pseudonym(hospital, rec["PatientID"])
            cur = vault.execute(
                "INSERT OR IGNORE INTO patients (patient_key, hospital, patient_id, patient_name, birth_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, hospital, rec["PatientID"], rec["PatientName"], rec["PatientBirthDate"]),
            )
            n_patients += cur.rowcount
            cur = clinical.execute(
                "INSERT INTO studies (hospital, study_id, study_uid, patient_key, age_years, sex, "
                "study_date, modality, body_part, diagnosis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hospital,
                    rec["StudyID"],
                    rec["StudyInstanceUID"],
                    key,
                    age_to_years(rec["PatientAge"]),
                    rec["PatientSex"],
                    rec["StudyDate"],
                    rec["Modality"],
                    rec["BodyPartExamined"],
                    rec["Diagnosis"],
                ),
            )
            clinical.execute(
                "INSERT INTO studies_fts (rowid, diagnosis) VALUES (?, ?)",
                (cur.lastrowid, rec["Diagnosis"]),
            )
            n_studies += 1

    clinical.commit()
    vault.commit()
    clinical.close()
    vault.close()
    print(f"Loaded {n_studies} studies (clinical.db) and {n_patients} patients (vault.db).")


if __name__ == "__main__":
    main()
