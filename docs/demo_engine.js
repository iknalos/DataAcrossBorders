// Client-side demo engine — mirrors the FastAPI aggregator so the GitHub Pages
// site works with no backend. Operates on window.DAB_SAMPLE (synthetic data).
// Response shapes match the real API so the UI code is identical either way.
(function () {
  const K = 5;
  const AGE_BANDS = [[0,1],[1,5],[5,12],[12,18],[18,40],[40,65],[65,90],[90,999]];
  const USERS = {
    "dr.chen":  {password:"demo123", role:"clinician",  display:"Dr. Emily Chen (BCH Neurology)"},
    "res.kim":  {password:"demo123", role:"researcher", display:"M. Kim (Research Fellow)"},
    "admin":    {password:"demo123", role:"admin",      display:"Federation Admin"},
  };

  const CONCEPT_GROUPS = [
    ["myocardial infarction","heart attack","cardiac infarction","mi","stemi","nstemi"],
    ["heart failure","cardiac failure","congestive heart failure","chf"],
    ["cardiomegaly","enlarged heart"],
    ["atrial fibrillation","afib","a-fib"],
    ["hypoplastic left heart syndrome","hlhs"],
    ["tetralogy of fallot","tof"],
    ["congenital heart disease","congenital heart defect","chd"],
    ["patent ductus arteriosus","pda"],
    ["ventricular septal defect","vsd"],
    ["atrial septal defect","asd"],
    ["pericardial effusion","fluid around the heart"],
    ["stroke","cerebrovascular accident","cva","brain attack"],
    ["ischemic infarct","ischaemic infarct","cerebral infarct","infarction","infarct"],
    ["hydrocephalus","ventriculomegaly","enlarged ventricles","water on the brain"],
    ["hemorrhage","haemorrhage","bleed","bleeding","hematoma","haematoma"],
    ["intracranial hemorrhage","ich","brain bleed"],
    ["middle cerebral artery","mca"],
    ["traumatic brain injury","tbi","head injury"],
    ["edema","oedema","swelling"],
    ["mass","tumor","tumour","neoplasm","lesion"],
    ["hypoxic ischemic encephalopathy","hie"],
    ["seizure","epilepsy","convulsion"],
    ["thrombus","clot","thrombosis","blood clot"],
    ["embolism","embolus"],
    ["stenosis","narrowing"],
    ["aneurysm","vascular dilatation"],
    ["fetal","foetal"],
    ["intrauterine growth restriction","iugr","fetal growth restriction","fgr","growth restriction"],
    ["oligohydramnios","low amniotic fluid"],
    ["polyhydramnios","excess amniotic fluid"],
    ["neural tube defect","ntd","spina bifida"],
    ["congenital diaphragmatic hernia","cdh"],
  ];
  const FORM_TO_CONCEPT = {};
  for (const g of CONCEPT_GROUPS) for (const f of g) FORM_TO_CONCEPT[f] = g;
  const PHRASES = Object.keys(FORM_TO_CONCEPT).filter(f => f.includes(" ")).sort((a,b)=>b.length-a.length);

  function expandQuery(raw) {
    let q = (raw || "").toLowerCase().trim();
    if (!q) return {groups: [], added: []};
    const groups = [], added = new Set();
    for (const phrase of PHRASES) {
      const re = new RegExp("\\b" + phrase.replace(/[.*+?^${}()|[\]\\]/g,"\\$&") + "\\b");
      if (re.test(q)) {
        const c = FORM_TO_CONCEPT[phrase];
        groups.push(c);
        c.forEach(f => { if (f !== phrase) added.add(f); });
        q = q.replace(re, " ");
      }
    }
    for (const tok of (q.match(/[a-z0-9\-]+/g) || [])) {
      if (FORM_TO_CONCEPT[tok]) {
        const c = FORM_TO_CONCEPT[tok];
        groups.push(c);
        c.forEach(f => { if (f !== tok) added.add(f); });
      } else {
        groups.push([tok]);
      }
    }
    return {groups, added: [...added].sort()};
  }

  // Whole-word matching (mirrors the server's word-tokenized FTS, so short
  // abbreviations like "mi" don't match inside "prominent"). Words >= 5 chars get
  // a prefix/stem match ("infarct" -> "infarction"); shorter forms match exactly.
  const _patCache = {};
  function formPattern(form) {
    if (_patCache[form]) return _patCache[form];
    const esc = form.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    _patCache[form] = form.length < 5 ? `\\b${esc}\\b` : `\\b${esc}[a-z]*\\b`;
    return _patCache[form];
  }
  function countMatches(d, form) {
    return (d.match(new RegExp(formPattern(form), "g")) || []).length;
  }
  function matchAndScore(diag, groups) {
    const d = diag.toLowerCase();
    let score = 0;
    for (const group of groups) {
      let best = 0;
      for (const form of group) { const c = countMatches(d, form); if (c > best) best = c; }
      if (best === 0) return -1;   // AND across groups: every group must hit
      score += best;
    }
    return score;
  }

  const SCRUB = [
    [/\b[A-Z][a-zA-Z\-]+\^[A-Z][a-zA-Z\-]+\b/g, "[NAME]"],
    [/\b\d{4}-\d{2}-\d{2}\b/g, "[DATE]"],
    [/\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/g, "[DATE]"],
    [/\b(?:19|20)\d{6}\b/g, "[DATE]"],
    [/\b[A-Z]{2,4}-\d{3,6}\b/g, "[ID]"],
    [/\b\d+(?:\.\d+){4,}\b/g, "[UID]"],
    [/\b\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/g, "[PHONE]"],
    [/\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g, "[EMAIL]"],
  ];
  function scrub(t) { for (const [re, r] of SCRUB) t = t.replace(re, r); return t; }

  function capAge(a) { return a > 89 ? 90 : a; }
  function displayAge(a) { return a > 89 ? "90+" : String(a); }
  function displayName(pn) { const p = pn.split("^").filter(Boolean); return p.length<=1 ? p[0] : p[0]+", "+p.slice(1).join(" "); }
  function displayDate(d) { return d.length===8 ? d.slice(0,4)+"-"+d.slice(4,6)+"-"+d.slice(6) : d; }
  function bandLabel(lo, hi) { return lo>=90 ? "90+" : lo+"-"+hi; }

  function applyFilters(p) {
    const q = p.get("q");
    const exp = expandQuery(q);
    let rows = window.DAB_SAMPLE.map(r => ({r, score: q ? matchAndScore(r.diagnosis, exp.groups) : 0}))
                                .filter(x => x.score >= 0);
    const num = (k) => p.get(k) !== null ? parseFloat(p.get(k)) : null;
    const eq  = (k) => p.get(k) ? p.get(k).toUpperCase() : null;
    const amin=num("age_min"), amax=num("age_max"), sex=eq("sex"), mod=eq("modality"), bp=eq("body_part"), hosp=eq("hospital");
    const category = p.get("category");
    rows = rows.filter(({r}) =>
      (amin===null || r.age_years>=amin) && (amax===null || r.age_years<=amax) &&
      (!sex || r.sex===sex) && (!mod || r.modality===mod) && (!bp || r.body_part===bp) &&
      (!hosp || r.hospital===hosp) && (!category || r.generic_category===category));

    // Status-aware entity search: default 'present' so ruled-out findings don't match.
    const finding = p.get("finding");
    if (finding) {
      const fl = finding.toLowerCase(), st = p.get("finding_status") || "present";
      rows = rows.filter(({r}) => (r.findings||[]).some(t =>
        t.value.toLowerCase().includes(fl) && (st==="any" || t.status===st)));
    }
    return {rows, expanded: exp.added, ranked: !!q};
  }

  function suppressDimension(counts) {
    const hidden = new Set(Object.entries(counts).filter(([,v]) => v>0 && v<K).map(([k])=>k));
    const visible = Object.entries(counts).filter(([k]) => !hidden.has(k));
    if (hidden.size === 1 && visible.length) {
      visible.sort((a,b)=>a[1]-b[1]); hidden.add(visible[0][0]);
    }
    const out = {};
    for (const [k,v] of Object.entries(counts)) out[k] = hidden.has(k) ? ("<"+K) : v;
    return out;
  }

  const AUDIT = [];
  let SESSION = null;

  function record(endpoint, params, count, flags) {
    AUDIT.unshift({ts_utc: new Date().toISOString().slice(0,19)+"Z", username: SESSION?SESSION.username:"?",
                   role: SESSION?SESSION.role:"?", endpoint, params_json: JSON.stringify(params),
                   result_count: count, flags: flags||""});
  }

  function differencingFlag(endpoint, params) {
    const prev = AUDIT.find(e => e.username===SESSION.username && e.endpoint==="/api/discover");
    if (!prev) return "";
    const a = Object.entries(params), b = Object.entries(JSON.parse(prev.params_json));
    const setA = new Set(a.map(x=>x.join("="))), setB = new Set(b.map(x=>x.join("=")));
    const diff = [...setA].filter(x=>!setB.has(x)).concat([...setB].filter(x=>!setA.has(x)));
    const shared = a.some(([k])=>b.find(([k2])=>k2===k));
    return (diff.length>0 && diff.length<=2 && shared) ? "possible-differencing" : "";
  }

  function paramsObj(p) { const o={}; for (const [k,v] of p) if (v!=="") o[k]=v; return o; }

  window.DEMO = {
    login(username, password) {
      const u = USERS[username];
      if (!u || u.password !== password) { const e = new Error("Invalid username or password."); e.status=401; throw e; }
      SESSION = {username, role: u.role};
      return {access_token: "demo."+u.role, token_type: "bearer", role: u.role, display: u.display};
    },
    discover(p) {
      const {rows, expanded} = applyFilters(p);
      const total = rows.length;
      const hosp = {}; for (const {r} of rows) hosp[r.hospital]=(hosp[r.hospital]||0)+1;
      const bands = {};
      for (const [lo,hi] of AGE_BANDS) { const n = rows.filter(({r})=>r.age_years>=lo&&r.age_years<hi).length; if (n) bands[bandLabel(lo,hi)]=n; }
      const po = paramsObj(p);
      const flags = differencingFlag("/api/discover", po);
      record("/api/discover", po, total, flags);
      return {tier:"discovery", exists: total>0, granularity:"count", k_anonymity_floor:K,
              total_matches: (total===0||total>=K)?total:("<"+K),
              by_hospital: suppressDimension(hosp), by_age_band: suppressDimension(bands),
              expanded_terms: expanded,
              note:"Counts below the k-anonymity floor are suppressed, with complementary suppression."};
    },
    search(p, format) {
      const role = SESSION.role;
      let {rows, expanded, ranked} = applyFilters(p);
      rows.sort(ranked ? (a,b)=>b.score-a.score : (a,b)=> (a.r.hospital+a.r.study_id).localeCompare(b.r.hospital+b.r.study_id));
      const total = rows.length;
      const limit = parseInt(p.get("limit")||"25"), offset = parseInt(p.get("offset")||"0");
      const page = rows.slice(offset, offset+limit);
      const results = page.map(({r}) => {
        let diag = r.diagnosis;
        let patient = {pseudonym: r.patient_key};
        if (role === "researcher") diag = scrub(diag);
        else if (role === "clinician") patient = {name: displayName(r.patient_name), patient_id: r.patient_id, birth_year: r.birth_date.slice(0,4)};
        else patient = {name: displayName(r.patient_name), patient_id: r.patient_id, birth_date: displayDate(r.birth_date)};
        return {hospital:r.hospital, study_id:r.study_id, patient, age: displayAge(capAge(r.age_years)),
                sex:r.sex, study_date: displayDate(r.study_date), modality:r.modality, body_part:r.body_part,
                category: r.generic_category, findings: r.findings||[], diagnosis:diag};
      });
      record("/api/search ("+(format||"json")+")", paramsObj(p), results.length, "");
      if (format === "csv") {
        const pii = role==="researcher" ? ["pseudonym"] : role==="clinician" ? ["name","patient_id","birth_year"] : ["name","patient_id","birth_date"];
        const cols = ["hospital","study_id",...pii,"age","sex","study_date","modality","body_part","category","findings_present","diagnosis"];
        const esc = v => `"${String(v==null?"":v).replace(/"/g,'""')}"`;
        const lines = [cols.join(",")];
        for (const res of results) {
          const present = (res.findings||[]).filter(t=>t.dimension==="finding_type"&&t.status==="present").map(t=>t.value).join("; ");
          const row = {...res, ...res.patient, findings_present: present};
          lines.push(cols.map(c=>esc(row[c])).join(","));
        }
        return lines.join("\n");
      }
      return {tier:"records", role, total_matches: total, returned: results.length, offset, expanded_terms: expanded, results};
    },
    audit() {
      if (SESSION.role !== "admin") { const e=new Error("Audit log is admin-only."); e.status=403; throw e; }
      return {entries: AUDIT.slice(0,50)};
    },
    verify() {
      if (SESSION.role !== "admin") { const e=new Error("Integrity report is admin-only."); e.status=403; throw e; }
      const n = window.DAB_SAMPLE.length;
      const checks = {study_count_matches_manifest:true, fts_index_consistent:true,
                      referential_integrity:true, row_hashes_valid:true, dataset_digest_matches_manifest:true};
      let h = 0; for (const r of window.DAB_SAMPLE) for (const ch of (r.patient_key+r.study_id)) h=(h*31+ch.charCodeAt(0))>>>0;
      const digest = ("00000000"+h.toString(16)).slice(-8).repeat(8);
      return {status:"PASS", checks,
              manifest:{study_count:n, patient_count:n, generated_utc:"(client-side sample)", dataset_digest:digest},
              recomputed_digest: digest};
    },
  };
})();
