"""ETL: pull all studies from the three running hospital nodes and load them into
two physically separate SQLite databases, with validation and integrity checks.

  vault.db     PII only (name, MRN, birth date)  -> schema_vault.sql
  clinical.db  de-identified studies + FTS5 index + audit + manifest -> schema_clinical.sql

Data-integrity guarantees:
  * every incoming record is schema-validated (Pydantic) before it is written;
  * each study row carries a sha256 row_hash of its canonical fields;
  * after load, referential integrity (every study -> a vault patient), row
    counts, and FTS consistency are verified — the ETL aborts on any mismatch;
  * a manifest row records counts + a dataset digest for /api/verify.

Re-running is idempotent (databases are rebuilt from scratch).

Usage:  python etl.py          (nodes must be running on :8001-:8003)
"""

import hashlib
import hmac
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, field_validator

BASE = Path(__file__).parent
NODES = {
    "BCH": "http://localhost:8001",
    "MGH": "http://localhost:8002",
    "BWH": "http://localhost:8003",
}
# Keyed pseudonymization. Externalize in production (KMS); the demo default keeps
# the repo runnable. Without this key the pseudonyms cannot be reversed or rebuilt.
PSEUDONYM_SECRET = os.environ.get("DAB_PSEUDONYM_SECRET", "dab-demo-pseudonym-secret").encode()


class IncomingStudy(BaseModel):
    """Validation contract for a record served by a hospital node."""
    PatientName: str
    PatientID: str
    PatientBirthDate: str
    PatientAge: str
    PatientSex: str
    InstitutionName: str
    StudyID: str
    StudyInstanceUID: str
    StudyDate: str
    Modality: str
    BodyPartExamined: str
    Diagnosis: str

    @field_validator("PatientBirthDate", "StudyDate")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        if not (len(v) == 8 and v.isdigit()):
            raise ValueError(f"expected YYYYMMDD, got {v!r}")
        return v

    @field_validator("PatientSex")
    @classmethod
    def _valid_sex(cls, v: str) -> str:
        if v not in ("M", "F"):
            raise ValueError(f"unexpected sex {v!r}")
        return v

    @field_validator("PatientAge")
    @classmethod
    def _valid_age(cls, v: str) -> str:
        if not (len(v) == 4 and v[:3].isdigit() and v[3].upper() in "YMD"):
            raise ValueError(f"expected NNN[Y|M|D], got {v!r}")
        return v


def age_to_years(age: str) -> float:
    n, unit = int(age[:-1]), age[-1].upper()
    return round({"Y": n * 1.0, "M": n / 12.0, "D": n / 365.25}[unit], 3)


def pseudonym(hospital: str, patient_id: str) -> str:
    msg = f"{hospital}|{patient_id}".encode()
    return hmac.new(PSEUDONYM_SECRET, msg, hashlib.sha256).hexdigest()[:16]


def row_hash(*fields) -> str:
    canon = "\x1f".join(str(f) for f in fields)
    return hashlib.sha256(canon.encode()).hexdigest()


def fetch_all() -> dict[str, list[dict]]:
    out = {}
    with httpx.Client(timeout=30) as client:
        for hospital, base_url in NODES.items():
            r = client.get(f"{base_url}/api/studies")
            r.raise_for_status()
            out[hospital] = r.json()
            print(f"  {hospital}: {len(out[hospital])} studies fetched")
    return out


LABELS_DIR = BASE.parent / "labels"
LABEL_FILES = {"BCH": "bch_labeled.json", "MGH": "mgh_labeled.json", "BWH": "bwh_labeled.json"}
VALID_STATUS = {"present", "absent", "uncertain"}
VALID_DIMENSION = {"location", "finding_type", "size"}


def load_labels() -> dict[tuple[str, str], dict]:
    """Map (hospital, StudyID) -> {GenericCategory, FindingTags} from the offline
    LLM_output labels. Enrichment only — the node data stays authoritative for the
    original 12 fields and for integrity."""
    labels: dict[tuple[str, str], dict] = {}
    for hospital, fname in LABEL_FILES.items():
        path = LABELS_DIR / fname
        if not path.exists():
            print(f"  NOTE: no label file for {hospital} ({path.name}); studies load without tags.", file=sys.stderr)
            continue
        for rec in json.loads(path.read_text(encoding="utf-8")):
            tags = []
            for t in rec.get("FindingTags", []):
                dim, val, st = t.get("dimension"), t.get("value"), t.get("status")
                if dim in VALID_DIMENSION and val and st in VALID_STATUS:
                    tags.append((dim, val, st))
            labels[(hospital, rec["StudyID"])] = {
                "category": rec.get("GenericCategory", ""),
                "tags": tags,
            }
        print(f"  {hospital}: {sum(1 for k in labels if k[0]==hospital)} labeled records loaded")
    return labels


def verify_integrity(clinical: sqlite3.Connection, vault: sqlite3.Connection) -> None:
    """Abort the ETL if any integrity invariant fails."""
    problems = []
    n_studies = clinical.execute("SELECT COUNT(*) FROM studies").fetchone()[0]
    n_fts = clinical.execute("SELECT COUNT(*) FROM studies_fts").fetchone()[0]
    if n_studies != n_fts:
        problems.append(f"FTS row count {n_fts} != studies {n_studies}")

    vault_keys = {r[0] for r in vault.execute("SELECT patient_key FROM patients")}
    study_keys = {r[0] for r in clinical.execute("SELECT DISTINCT patient_key FROM studies")}
    orphans = study_keys - vault_keys
    if orphans:
        problems.append(f"{len(orphans)} studies reference a patient absent from the vault")

    # Recompute every row_hash from stored columns (detects silent corruption).
    bad = 0
    for r in clinical.execute(
        "SELECT hospital, study_id, study_uid, patient_key, age_years, sex, "
        "study_date, modality, body_part, generic_category, diagnosis, row_hash FROM studies"
    ):
        expect = row_hash(*r[:11])
        if expect != r[11]:
            bad += 1
    if bad:
        problems.append(f"{bad} rows failed hash verification")

    # Every finding tag must point at a real study (referential integrity).
    tag_orphans = clinical.execute(
        "SELECT COUNT(*) FROM finding_tags t LEFT JOIN studies s ON s.study_key=t.study_key "
        "WHERE s.study_key IS NULL").fetchone()[0]
    if tag_orphans:
        problems.append(f"{tag_orphans} finding tags reference a missing study")

    if problems:
        sys.exit("INTEGRITY CHECK FAILED:\n  - " + "\n  - ".join(problems))
    n_tags = clinical.execute("SELECT COUNT(*) FROM finding_tags").fetchone()[0]
    print(f"Integrity OK: {n_studies} studies, {n_tags} tags, FTS consistent, no orphans, all hashes valid.")


def main() -> None:
    print("Fetching from hospital nodes...")
    try:
        data = fetch_all()
    except httpx.ConnectError as e:
        sys.exit(f"ERROR: a hospital node is not reachable ({e}). Start all three nodes first.")

    print("Loading LLM labels (GenericCategory + FindingTags)...")
    labels = load_labels()

    for db in ("clinical.db", "vault.db"):
        for suffix in ("", "-wal", "-shm"):
            (BASE / (db + suffix)).unlink(missing_ok=True)

    clinical = sqlite3.connect(BASE / "clinical.db")
    vault = sqlite3.connect(BASE / "vault.db")
    clinical.executescript((BASE / "schema_clinical.sql").read_text())
    vault.executescript((BASE / "schema_vault.sql").read_text())

    n_studies = n_patients = n_invalid = n_tags = n_untagged = 0
    all_hashes = []
    for hospital, records in data.items():
        for raw in records:
            try:
                rec = IncomingStudy(**raw)
            except Exception as e:  # noqa: BLE001 - report and skip malformed rows
                n_invalid += 1
                print(f"  SKIP invalid record from {hospital}: {e}", file=sys.stderr)
                continue

            key = pseudonym(hospital, rec.PatientID)
            cur = vault.execute(
                "INSERT OR IGNORE INTO patients (patient_key, hospital, patient_id, patient_name, birth_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, hospital, rec.PatientID, rec.PatientName, rec.PatientBirthDate),
            )
            n_patients += cur.rowcount

            label = labels.get((hospital, rec.StudyID), {"category": "", "tags": []})
            category = label["category"]
            if not label["tags"] and not category:
                n_untagged += 1

            age = age_to_years(rec.PatientAge)
            h = row_hash(hospital, rec.StudyID, rec.StudyInstanceUID, key, age, rec.PatientSex,
                         rec.StudyDate, rec.Modality, rec.BodyPartExamined, category, rec.Diagnosis)
            all_hashes.append(h)
            cur = clinical.execute(
                "INSERT INTO studies (hospital, study_id, study_uid, patient_key, age_years, sex, "
                "study_date, modality, body_part, generic_category, diagnosis, row_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hospital, rec.StudyID, rec.StudyInstanceUID, key, age, rec.PatientSex,
                 rec.StudyDate, rec.Modality, rec.BodyPartExamined, category, rec.Diagnosis, h),
            )
            study_key = cur.lastrowid
            clinical.execute("INSERT INTO studies_fts (rowid, diagnosis) VALUES (?, ?)", (study_key, rec.Diagnosis))
            for dim, val, st in label["tags"]:
                clinical.execute(
                    "INSERT INTO finding_tags (study_key, dimension, value, value_lc, status) VALUES (?, ?, ?, ?, ?)",
                    (study_key, dim, val, val.lower(), st))
                n_tags += 1
            n_studies += 1

    digest = hashlib.sha256("".join(sorted(all_hashes)).encode()).hexdigest()
    clinical.execute(
        "INSERT INTO data_manifest (id, generated_utc, study_count, patient_count, dataset_digest, source_nodes) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), n_studies, n_patients,
         digest, json.dumps(list(NODES))),
    )
    clinical.commit()
    vault.commit()

    verify_integrity(clinical, vault)
    clinical.close()
    vault.close()

    if n_invalid:
        print(f"WARNING: skipped {n_invalid} invalid record(s).", file=sys.stderr)
    if n_untagged:
        print(f"NOTE: {n_untagged} studies had no matching label.", file=sys.stderr)
    print(f"Loaded {n_studies} studies (clinical.db) and {n_patients} patients (vault.db).")
    print(f"Loaded {n_tags} finding tags.")
    print(f"Dataset digest: {digest[:16]}…")


if __name__ == "__main__":
    main()
