"""Automated guarantee tests for the DataAcrossBorders aggregator.

Covers the invariants that must never regress: data integrity, the privacy
tiers, k-anonymity (including differencing safety), synonym expansion, Safe
Harbor scrubbing, access control, and input robustness.

Run from the solution/ directory (databases must be built by etl.py first):
    pytest -q
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SOLUTION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOLUTION))

import app as app_module  # noqa: E402
from deident import scrub_text, cap_age  # noqa: E402
from synonyms import expand_query  # noqa: E402

client = TestClient(app_module.app)

pytestmark = pytest.mark.skipif(
    not (SOLUTION / "clinical.db").exists(),
    reason="databases not built; run `python etl.py` first",
)


def token(username: str) -> str:
    r = client.post("/auth/login", json={"username": username, "password": "demo123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth(username: str) -> dict:
    return {"Authorization": f"Bearer {token(username)}"}


# --------------------------------------------------------------------------- #
# Health & integrity
# --------------------------------------------------------------------------- #
def test_health_reports_full_dataset():
    d = client.get("/health").json()
    assert d["status"] == "healthy"
    assert d["studies_indexed"] == 2700
    assert set(d["hospitals"]) == {"BCH", "MGH", "BWH"}


def test_integrity_verify_passes():
    d = client.get("/api/verify", headers=auth("admin")).json()
    assert d["status"] == "PASS"
    assert all(d["checks"].values()), d["checks"]
    assert d["recomputed_digest"] == d["manifest"]["dataset_digest"]


# --------------------------------------------------------------------------- #
# Authentication & access control
# --------------------------------------------------------------------------- #
def test_wrong_password_rejected():
    assert client.post("/auth/login", json={"username": "res.kim", "password": "nope"}).status_code == 401


def test_missing_and_junk_tokens_rejected():
    assert client.get("/api/search?q=stroke").status_code == 401
    assert client.get("/api/search?q=stroke", headers={"Authorization": "Bearer junk"}).status_code == 401


@pytest.mark.parametrize("endpoint", ["/api/verify", "/api/audit"])
def test_admin_only_endpoints_forbidden_for_others(endpoint):
    assert client.get(endpoint, headers=auth("res.kim")).status_code == 403
    assert client.get(endpoint, headers=auth("dr.chen")).status_code == 403
    assert client.get(endpoint, headers=auth("admin")).status_code == 200


# --------------------------------------------------------------------------- #
# Privacy tiers (the core requirement)
# --------------------------------------------------------------------------- #
def test_researcher_gets_pseudonym_only():
    r = client.get("/api/search?q=hydrocephalus&limit=1", headers=auth("res.kim")).json()
    p = r["results"][0]["patient"]
    assert set(p) == {"pseudonym"}
    assert "name" not in p and "patient_id" not in p


def test_clinician_gets_name_and_birth_year_only():
    p = client.get("/api/search?q=hydrocephalus&limit=1", headers=auth("dr.chen")).json()["results"][0]["patient"]
    assert "name" in p and "birth_year" in p
    assert "birth_date" not in p          # full DOB withheld from clinician
    assert "^" not in p["name"]           # DICOM caret formatted out


def test_admin_gets_full_dob():
    p = client.get("/api/search?q=hydrocephalus&limit=1", headers=auth("admin")).json()["results"][0]["patient"]
    assert "birth_date" in p and len(p["birth_date"]) == 10   # YYYY-MM-DD


def test_csv_export_matches_role_redaction():
    r = client.get("/api/search?q=hydrocephalus&limit=1&format=csv", headers=auth("res.kim"))
    header = r.text.splitlines()[0]
    assert "pseudonym" in header
    assert "name" not in header and "birth_date" not in header


# --------------------------------------------------------------------------- #
# Search quality: medical synonym expansion + ranking
# --------------------------------------------------------------------------- #
def test_synonym_expansion_unit():
    match, added = expand_query("heart attack")
    assert "myocardial infarction" in match
    assert "myocardial infarction" in added


def test_heart_attack_matches_myocardial_infarction():
    hdrs = auth("res.kim")
    a = client.get("/api/search?q=myocardial infarction&limit=1", headers=hdrs).json()["total_matches"]
    b = client.get("/api/search?q=heart attack&limit=1", headers=hdrs).json()["total_matches"]
    assert a == b and a > 0


def test_nonsense_query_returns_nothing_without_error():
    r = client.get("/api/search?q=zzzxqqnotaword&limit=1", headers=auth("res.kim"))
    assert r.status_code == 200 and r.json()["total_matches"] == 0


@pytest.mark.parametrize("q", ['"unclosed', "a AND OR b", "term)(", "NEAR(x", "*", "^^^"])
def test_malformed_fts_never_500s(q):
    assert client.get("/api/search", params={"q": q, "limit": 1}, headers=auth("res.kim")).status_code == 200


def test_pagination_beyond_end_is_empty_not_error():
    r = client.get("/api/search?q=stroke&limit=5&offset=99999", headers=auth("res.kim")).json()
    assert r["returned"] == 0 and r["total_matches"] >= 0


# --------------------------------------------------------------------------- #
# Safe Harbor free-text scrubbing (researcher tier)
# --------------------------------------------------------------------------- #
def test_scrub_removes_identifiers_unit():
    text = "Patient Smith^BabyBoy (CHB-66291), DOB 20260210, seen 2026-07-15, ph 617-555-1234."
    scrubbed, n = scrub_text(text)
    assert n >= 4
    for leaked in ["Smith^BabyBoy", "CHB-66291", "20260210", "2026-07-15", "617-555-1234"]:
        assert leaked not in scrubbed


def test_cap_age_over_89():
    assert cap_age(93) == 90.0 and cap_age(40) == 40.0


# --------------------------------------------------------------------------- #
# k-anonymity discovery: suppression + differencing safety
# --------------------------------------------------------------------------- #
def test_small_cells_are_suppressed():
    d = client.get("/api/discover?q=aneurysm&body_part=BRAIN", headers=auth("res.kim")).json()
    values = list(d["by_hospital"].values()) + list(d["by_age_band"].values())
    # every reported number is either suppressed marker or >= floor
    for v in values:
        assert v == f"<{app_module.K}" or (isinstance(v, int) and v >= app_module.K)


def test_complementary_suppression_prevents_single_hidden_cell():
    d = client.get("/api/discover?q=aneurysm&body_part=BRAIN", headers=auth("res.kim")).json()
    hidden = [v for v in d["by_hospital"].values() if v == f"<{app_module.K}"]
    # if anything in the dimension is hidden, at least two cells must be, so the
    # single value can't be recovered by subtracting from the total.
    assert len(hidden) != 1


def test_differencing_probe_is_flagged():
    hdrs = auth("res.kim")
    client.get("/api/discover?body_part=HEART&hospital=MGH", headers=hdrs)
    client.get("/api/discover?body_part=HEART&hospital=MGH&sex=F", headers=hdrs)
    entries = client.get("/api/audit?limit=1", headers=auth("admin")).json()["entries"]
    assert entries[0]["flags"] == "possible-differencing"
