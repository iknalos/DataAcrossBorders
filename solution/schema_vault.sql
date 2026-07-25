-- vault.db — the PII vault. Direct patient identifiers live here and ONLY here.
-- Joined to clinical.db via patient_key (HMAC pseudonym) at query time,
-- and only for roles authorized to re-identify (clinician, admin).
-- Researcher-role requests never open a connection to this database.

CREATE TABLE IF NOT EXISTS patients (
    patient_key  TEXT PRIMARY KEY,   -- HMAC-SHA256(secret, hospital|PatientID), first 16 hex
    hospital     TEXT NOT NULL,
    patient_id   TEXT NOT NULL,      -- original MRN, e.g. CHB-99214
    patient_name TEXT NOT NULL,      -- LastName^FirstName (DICOM PN format)
    birth_date   TEXT NOT NULL,      -- YYYYMMDD
    UNIQUE (hospital, patient_id)
);
