-- clinical.db — de-identified clinical data. Contains NO direct identifiers.
-- The only link to patient identity is patient_key, an opaque HMAC pseudonym
-- whose reverse mapping lives in a physically separate database (vault.db).
-- Schema is MySQL-portable (swap FTS5 for FULLTEXT INDEX, INTEGER PRIMARY KEY for AUTO_INCREMENT).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS studies (
    study_key   INTEGER PRIMARY KEY,          -- surrogate key (rowid)
    hospital    TEXT NOT NULL,                -- BCH / MGH / BWH
    study_id    TEXT NOT NULL,                -- e.g. BR-7721 (unique only per hospital)
    study_uid   TEXT NOT NULL,                -- DICOM StudyInstanceUID
    patient_key TEXT NOT NULL,                -- opaque pseudonym -> vault.db
    age_years   REAL NOT NULL,                -- normalized from 007Y / 011M / 005D
    sex         TEXT NOT NULL,                -- M / F
    study_date  TEXT NOT NULL,                -- YYYYMMDD
    modality    TEXT NOT NULL,                -- MR / CT / US ...
    body_part   TEXT NOT NULL,                -- BRAIN / HEART / FETAL
    diagnosis   TEXT NOT NULL,                -- full radiology report text
    row_hash    TEXT NOT NULL,                -- sha256 of canonical fields (tamper/corruption check)
    UNIQUE (hospital, study_id)
);

CREATE INDEX IF NOT EXISTS idx_studies_body_part ON studies (body_part);
CREATE INDEX IF NOT EXISTS idx_studies_modality  ON studies (modality);
CREATE INDEX IF NOT EXISTS idx_studies_age       ON studies (age_years);
CREATE INDEX IF NOT EXISTS idx_studies_hospital  ON studies (hospital);
CREATE INDEX IF NOT EXISTS idx_studies_patient   ON studies (patient_key);

-- FTS5 inverted index over the diagnosis text (the "indexing" answer).
CREATE VIRTUAL TABLE IF NOT EXISTS studies_fts USING fts5(
    diagnosis,
    content='studies',
    content_rowid='study_key',
    tokenize = 'porter unicode61'      -- porter stemming: "infarction" matches "infarct"
);

-- Every query against the federation is recorded (data-access audit trail).
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY,
    ts_utc       TEXT NOT NULL,
    username     TEXT NOT NULL,
    role         TEXT NOT NULL,
    endpoint     TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    flags        TEXT NOT NULL DEFAULT ''     -- e.g. 'possible-differencing'
);

-- Integrity manifest: one row written at end of ETL, used by /api/verify.
CREATE TABLE IF NOT EXISTS data_manifest (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    generated_utc   TEXT NOT NULL,
    study_count     INTEGER NOT NULL,
    patient_count   INTEGER NOT NULL,
    dataset_digest  TEXT NOT NULL,            -- sha256 over all sorted row_hashes
    source_nodes    TEXT NOT NULL
);
