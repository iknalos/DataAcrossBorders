"""Free-text de-identification (HIPAA Safe Harbor, defense-in-depth).

Structured PII is already isolated in the vault, but unstructured `diagnosis`
text can still leak identifiers (names, dates, MRNs, phone numbers). Safe Harbor
requires removing all 18 identifier categories from free text and aggregating
ages over 89. This module scrubs the diagnosis before it is returned to roles
that are not permitted to see PII (i.e. researchers).

This is a rule-based scrubber suited to a demo; a production system would pair it
with a trained clinical NER de-identifier and a manual QA pass. Every scrub is
reported so the pipeline keeps a defensible audit trail.
"""

import re

_PATTERNS = [
    # DICOM person-name format Last^First
    (re.compile(r"\b[A-Z][a-zA-Z\-]+\^[A-Z][a-zA-Z\-]+\b"), "[NAME]"),
    # Dates: 2026-07-15, 07/15/2026, 20260715
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "[DATE]"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "[DATE]"),
    (re.compile(r"\b(?:19|20)\d{6}\b"), "[DATE]"),
    # Medical record numbers like CHB-99214, BR-7721
    (re.compile(r"\b[A-Z]{2,4}-\d{3,6}\b"), "[ID]"),
    # DICOM UID (dotted numeric)
    (re.compile(r"\b\d+(?:\.\d+){4,}\b"), "[UID]"),
    # US phone numbers
    (re.compile(r"\b\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"), "[PHONE]"),
    # Email / URL
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    # Ages over 89 must be aggregated (Safe Harbor)
    (re.compile(r"\b(9\d|1\d\d)\s*(?:-|\s)?year[\s-]?old\b", re.I), "90+ year-old"),
]


def scrub_text(text: str) -> tuple[str, int]:
    """Return (scrubbed_text, number_of_redactions)."""
    n = 0
    for pattern, repl in _PATTERNS:
        text, count = pattern.subn(repl, text)
        n += count
    return text, n


def cap_age(age_years: float) -> float:
    """Safe Harbor: ages over 89 are reported as 90 (aggregated)."""
    return 90.0 if age_years > 89 else age_years


def display_age(age_years: float) -> str:
    return "90+" if age_years > 89 else str(age_years)
