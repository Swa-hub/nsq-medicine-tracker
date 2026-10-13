"""Stage 3: clean the parsed rows.

* manufacturer -> company name, matching key, address, PIN, state, district
* drug -> generic name, brand, dosage form
* failure reason -> standard categories (assay, dissolution, sterility...)
* reporting lab / state -> tidy names
* revised lists replace the originals; the compiled 2017-2023 list is kept
  aside for cross-checks so nothing is counted twice
"""
from __future__ import annotations

import logging
import re

import pandas as pd

from . import config, geo
from .textutil import clean_ws, norm_key

log = logging.getLogger(__name__)

# ------------------------------------------------------------- manufacturers

_PREFIX = re.compile(r"^\s*(?:m\s*/\s*s\.?|messrs\.?|m/s)\s*", re.I)
_ADDRESS_START = re.compile(
    r",|\b(?:plot|khasra|survey|sy\.?\s*no|village|vill\.?|unit[\s\-]|sector|industrial|"
    r"phase|road|p\.?\s*o\.?|near|opp\.?|no\.\s*\d|distt|district|at\s*[:\-]|"
    r"\d{6}\b)", re.I)

_SUFFIX_RULES = [
    (r"\bprivate\s+limited\b|\bpvt\.?\s*ltd\.?|\(p\)\s*ltd\.?|\bp\.?\s*ltd\.?|\bpvt\b", " pvt ltd "),
    (r"\blimited\b", " ltd "),
    (r"\bpharmaceuticals?\b|\bpharmaceutics\b|\bpharma\b", " pharma "),
    (r"\blaborator(?:y|ies)\b|\blabs?\b", " labs "),
    (r"\blife\s*sciences?\b|\blifesciences?\b|\blifesceinces?\b", " lifesciences "),
    (r"\bhealth\s*care\b", " healthcare "),
    (r"\bformulations?\b", " formulations "),
    (r"\bindustries\b|\bindustry\b", " industries "),
    (r"\bremed(?:y|ies)\b", " remedies "),
    (r"\bparenterals?\b", " parenterals "),
    (r"\bmedi\s*care\b", " medicare "),
    (r"&", " and "),
]
# Words that tell us nothing about WHICH company it is
_STOP = {"pvt", "ltd", "the", "and", "india", "co", "company", "inc", "llp", "of"}


def split_manufacturer(raw: str) -> tuple[str, str]:
    """'M/s. ABC Pharma Pvt. Ltd., Plot 5, Baddi' -> ('ABC Pharma Pvt. Ltd.', 'Plot 5, Baddi')"""
    s = clean_ws(raw)
    s = re.sub(r"\(as claimed on (?:the )?label\)", "", s, flags=re.I)
    s = _PREFIX.sub("", s)
    m = _ADDRESS_START.search(s)
    if m and m.start() > 2:
        name, addr = s[:m.start()], s[m.start():]
    else:
        name, addr = s, ""
    return name.strip(" ,.-"), addr.strip(" ,.-")


def name_key(name: str) -> str:
    """Spelling-proof key: 'Arvex Life Sciences Private Limited' -> 'arvex lifesciences'"""
    s = " " + clean_ws(name).lower() + " "
    s = re.sub(r"\(.*?\)", " ", s) if not re.search(r"\(p\)", s) else s
    for pat, rep in _SUFFIX_RULES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"\bunit[\s\-]*(?:[ivx]+|\d+)\b", " ", s)
    s = norm_key(s)
    words = [w for w in s.split() if w not in _STOP]
    return " ".join(words)


def clean_manufacturer(raw: str) -> dict:
    name, addr = split_manufacturer(raw)
    full = clean_ws(raw)
    pin = geo.find_pin(full)
    state, how = geo.find_state(addr or full)
    district = geo.find_district(addr or full)
    if how in ("pin", "none") and district in geo.DISTRICT_STATE:
        state, how = geo.DISTRICT_STATE[district], "district"
    return {
        "mfr_name": name, "mfr_key": name_key(name), "mfr_address": addr,
        "mfr_pin": pin, "mfr_state": state, "mfr_state_source": how,
        "mfr_district": district,
    }


# ------------------------------------------------------------- drugs

_FORMS = [
    ("eye drops", r"eye\s*drops?|ophthalmic"),
    ("injection", r"injection|for\s+injection|infusion|\binj\b"),
    ("syrup", r"syrup|oral\s+solution|suspension|elixir|linctus"),
    ("capsule", r"capsules?|\bcap\b"),
    ("tablet", r"tablets?|\btab\b"),
    ("cream/gel", r"cream|gel|ointment|lotion"),
    ("powder", r"powder|sachet|granules"),
]
_PHARMACOPOEIA = r"\b(?:i\.?\s*p\.?|b\.?\s*p\.?|u\.?\s*s\.?\s*p\.?|e\.?\s*p\.?)(?=\s|$|[,(])"
_STRENGTH = r"\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|iu|%)(?:\s*/\s*\d*(?:\.\d+)?\s*(?:mg|mcg|g|ml|%))?(?:\s*w/[vw])?"


def clean_drug(raw: str) -> dict:
    s = clean_ws(raw)
    brand = ""
    m = re.search(r"\(([^()]{2,40})\)\s*$", s)
    if m:
        brand = m.group(1).strip()
        s = s[:m.start()].strip()
    low = s.lower()
    form = next((f for f, pat in _FORMS if re.search(pat, low)), "other")
    generic = low
    form_m = None
    for _, pat in _FORMS:
        form_m = re.search(pat, generic)
        if form_m:
            break
    if form_m:
        generic = generic[:form_m.start()]
    generic = re.sub(_PHARMACOPOEIA, " ", generic)
    generic = re.sub(_STRENGTH, " ", generic)
    generic = re.sub(r"gastro[\s\-]*resistant|extended\s+release|sustained\s+release|"
                     r"prolonged\s+release|\bsr\b|\ber\b|\bdt\b|film\s+coated|sterile|for\s*$", " ", generic)
    generic = re.sub(r"\s*(?:&|\band\b|\+|/|,)\s*", " + ", generic)
    generic = re.sub(r"[^a-z0-9+ ]", " ", generic)
    parts = [p.strip() for p in generic.split("+") if p.strip()]
    generic = " + ".join(sorted(set(parts))) if parts else norm_key(s)
    strength = re.search(_STRENGTH, s, re.I)
    return {"drug_generic": generic, "drug_brand": brand, "drug_form": form,
            "drug_strength": strength.group(0) if strength else ""}


# ------------------------------------------------------------- failure reasons

REASON_CATEGORIES = [
    ("spurious", r"spurious|disowned|not manufactured by|fake"),
    ("assay", r"\bassay\b|content of"),
    ("dissolution", r"dissolution"),
    ("disintegration", r"disintegration"),
    ("sterility", r"sterility|not sterile"),
    ("particulate matter", r"particulate|particles"),
    ("endotoxin / microbial", r"endotoxin|microbial|bacteri|fungal|pyrogen"),
    ("identification", r"identification|identity"),
    ("description", r"description|appearance|colour|color|odour"),
    ("uniformity / weight", r"uniformity|weight variation|average weight|content uniformity"),
    ("ph", r"\bph\b"),
    ("impurities", r"related substances|impurit|degradation"),
    ("labelling", r"label|misbrand"),
]


def reason_categories(raw: str) -> list[str]:
    t = clean_ws(raw).lower()
    cats = [c for c, pat in REASON_CATEGORIES if re.search(pat, t)]
    return cats or (["other"] if t else ["not stated"])


# ------------------------------------------------------------- labs

_LAB = re.compile(r"\b(?:cdl|cdtl|rdtl|ipc)\b|central|laborator|testing lab", re.I)


def clean_reporter(reported_by: str, drawn_by: str, section: str) -> dict:
    """Who tested the sample (a lab) and, for state lists, which state reported it.

    New lists: 'Reported by' holds a central lab (CDL, Kolkata) or a state name.
    Old lists: 'From' holds the lab and 'Drawn By' says who took the sample,
    e.g. 'Drug Control Department, Karnataka' -> state Karnataka."""
    rep, drawn = clean_ws(reported_by), clean_ws(drawn_by)
    lab = None
    if rep and _LAB.search(rep):
        lab = re.sub(r"central\s+drugs?\s+laboratory", "CDL", rep, flags=re.I)
        lab = re.sub(r"central\s+drugs?\s+testing\s+laboratory", "CDTL", lab, flags=re.I)
        lab = re.sub(r"regional\s+drugs?\s+testing\s+laboratory", "RDTL", lab, flags=re.I)
        lab = re.sub(r"\s*,\s*", ", ", lab)
    state = None
    if section == "state":
        source = drawn if (lab or not rep) else rep
        st, _ = geo.find_state(source)
        state = st or (source.split(",")[-1].strip().title() if source and not _LAB.search(source) else None)
    return {"reporting_state": state, "reporting_lab": lab}


# ------------------------------------------------------------- whole table

def apply_revisions(rows: pd.DataFrame) -> pd.DataFrame:
    """When CDSCO re-issues a month's list ('Revised'), keep only the newest
    version of that month's list of that kind. Compiled lists are set aside."""
    rows = rows.copy()
    rows["list_kind"] = rows["doc_type"].replace({"combined": "combined"})
    rows["is_revised"] = rows["is_revised"].fillna(False).astype(bool)
    month_key = ["report_year", "report_month", "list_kind"]
    latest = (rows[rows["doc_type"] != "compiled"]
              .groupby(month_key, dropna=False)["is_revised"].max().rename("month_has_revision"))
    rows = rows.join(latest, on=month_key)
    rows["superseded"] = rows["month_has_revision"].fillna(False) & ~rows["is_revised"]
    return rows


def clean_rows(paths: config.Paths) -> pd.DataFrame:
    rows = pd.read_parquet(paths.rows)
    log.info("Cleaning %d rows", len(rows))
    rows = apply_revisions(rows)

    mf = rows["manufacturer_raw"].fillna("").map(clean_manufacturer).apply(pd.Series)
    dr = rows["drug_name"].fillna("").map(clean_drug).apply(pd.Series)
    rep = rows.apply(lambda r: clean_reporter(r["reported_by"] or "", r["drawn_by"] or "", r["section"]),
                     axis=1).apply(pd.Series)
    cats = rows["reason_raw"].fillna("").map(reason_categories)
    out = pd.concat([rows, mf, dr, rep], axis=1)
    out["reason_categories"] = cats.map(lambda c: ";".join(c))
    out["reason_primary"] = cats.map(lambda c: c[0])
    out.loc[out["is_spurious"], "reason_primary"] = "spurious"
    out["batch_key"] = out["batch_no"].fillna("").map(lambda b: re.sub(r"[^A-Z0-9]", "", str(b).upper()))
    out["report_month_start"] = pd.to_datetime(
        dict(year=out["report_year"], month=out["report_month"], day=1), errors="coerce")
    out["in_facts"] = (~out["superseded"]) & (out["doc_type"] != "compiled") & out["report_month_start"].notna()
    out.to_parquet(paths.clean_rows, index=False)
    log.info("Clean rows: %d (%d kept as facts, %d superseded by a revised list)",
             len(out), int(out["in_facts"].sum()), int(out["superseded"].sum()))
    return out
