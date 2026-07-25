"""Medical query expansion (UMLS/SNOMED-style concept synonymy).

Pure keyword search can't know that "heart attack", "myocardial infarction" and
"MI" are the same concept. Clinical search engines solve this with query
expansion over a controlled vocabulary (UMLS/SNOMED CT). We ship a curated,
bidirectional concept map covering the three data domains present in this
federation (neuro / cardiac / fetal) — high precision, zero heavy dependencies.

expand_query("heart attack in newborn") ->
    FTS5 MATCH string with the cardiac-infarct concept OR-expanded, plus the
    list of surface forms that were added, for UI transparency.
"""

import re

# Each inner list is one clinical concept; all surface forms are treated as
# synonyms of each other. Multi-word forms are matched as phrases.
CONCEPT_GROUPS: list[list[str]] = [
    # --- Cardiac ---
    ["myocardial infarction", "heart attack", "cardiac infarction", "mi", "stemi", "nstemi"],
    ["heart failure", "cardiac failure", "congestive heart failure", "chf"],
    ["cardiomegaly", "enlarged heart"],
    ["atrial fibrillation", "afib", "a-fib"],
    ["hypoplastic left heart syndrome", "hlhs"],
    ["tetralogy of fallot", "tof"],
    ["congenital heart disease", "congenital heart defect", "chd"],
    ["patent ductus arteriosus", "pda"],
    ["ventricular septal defect", "vsd"],
    ["atrial septal defect", "asd"],
    ["pericardial effusion", "fluid around the heart"],
    # --- Neuro ---
    ["stroke", "cerebrovascular accident", "cva", "brain attack"],
    ["ischemic infarct", "ischaemic infarct", "cerebral infarct", "infarction", "infarct"],
    ["hydrocephalus", "ventriculomegaly", "enlarged ventricles", "water on the brain"],
    ["hemorrhage", "haemorrhage", "bleed", "bleeding", "hematoma", "haematoma"],
    ["intracranial hemorrhage", "ich", "brain bleed"],
    ["middle cerebral artery", "mca"],
    ["traumatic brain injury", "tbi", "head injury"],
    ["edema", "oedema", "swelling"],
    ["mass", "tumor", "tumour", "neoplasm", "lesion"],
    ["hypoxic ischemic encephalopathy", "hie"],
    ["seizure", "epilepsy", "convulsion"],
    # --- Vascular / general ---
    ["thrombus", "clot", "thrombosis", "blood clot"],
    ["embolism", "embolus"],
    ["stenosis", "narrowing"],
    ["aneurysm", "vascular dilatation"],
    # --- Fetal / obstetric ---
    ["fetal", "foetal"],
    ["intrauterine growth restriction", "iugr", "fetal growth restriction", "fgr", "growth restriction"],
    ["oligohydramnios", "low amniotic fluid"],
    ["polyhydramnios", "excess amniotic fluid"],
    ["neural tube defect", "ntd", "spina bifida"],
    ["congenital diaphragmatic hernia", "cdh"],
]

# Build lookup: surface form (lowercased) -> set of all forms in its concept.
_FORM_TO_CONCEPT: dict[str, list[str]] = {}
for group in CONCEPT_GROUPS:
    for form in group:
        _FORM_TO_CONCEPT[form.lower()] = group

# Multi-word phrases, longest first, so "heart attack" matches before "heart".
_PHRASES = sorted((f for f in _FORM_TO_CONCEPT if " " in f), key=len, reverse=True)


def _fts_group(forms: list[str]) -> str:
    """OR-group of quoted phrases for an FTS5 MATCH expression."""
    return "(" + " OR ".join(f'"{f}"' for f in forms) + ")"


def expand_query(raw: str) -> tuple[str, list[str]]:
    """Return (fts_match_expression, added_surface_forms).

    The result is an AND of groups; each group that maps to a known concept is
    OR-expanded to every synonym. Unknown terms are matched literally. Returns
    ("", []) for an empty query.
    """
    q = raw.lower().strip()
    if not q:
        return "", []

    groups: list[str] = []
    added: set[str] = set()

    # 1) Consume known multi-word phrases first.
    for phrase in _PHRASES:
        if re.search(r"\b" + re.escape(phrase) + r"\b", q):
            concept = _FORM_TO_CONCEPT[phrase]
            groups.append(_fts_group(concept))
            added.update(f for f in concept if f != phrase)
            q = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", q)

    # 2) Remaining single tokens.
    for tok in re.findall(r"[a-z0-9\-]+", q):
        if tok in _FORM_TO_CONCEPT:
            concept = _FORM_TO_CONCEPT[tok]
            groups.append(_fts_group(concept))
            added.update(f for f in concept if f != tok)
        else:
            groups.append(f'"{tok}"')

    if not groups:
        return "", []
    return " AND ".join(groups), sorted(added)
