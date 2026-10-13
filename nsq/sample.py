"""Generate a SAMPLE dataset that copies CDSCO's real formats, for demos and tests.

Everything here is invented: company names are made-up words, brands are
fictional and every PDF is stamped "SAMPLE DATA - NOT REAL". Towns, pin codes
and labs are real so the cleaning code is tested on realistic addresses.

What it produces (inside the data folder you pass, e.g. data_demo/):
* raw/pdfs/*.pdf      monthly lists in the OLD format (2022-2023: one combined
                      cell, stated totals) and the NEW format (2024 to Jul 2025:
                      separate central, state and spurious lists)
* raw/portal/*.json   NSQ-portal style records from Aug 2025 onward, with the
                      list of states that had not reported
* raw/alert_index.csv and raw/manifest.csv, as the real collect stage writes
* reference/sample_truth.csv  the true company and site for every batch, so
                      the entity-matching accuracy can be measured automatically
* reference/qr_brands.csv     invented "QR-coded top brands" for the demo
"""
from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

# Invented name parts. Combined they give made-up company names like "Quorvex".
_PRE = ["Quor", "Zent", "Vald", "Mirel", "Torn", "Plex", "Krov", "Selv", "Drax", "Obrin",
        "Fyndr", "Nuvar", "Ostr", "Brel", "Cavr", "Lumox", "Tesk", "Yvant", "Gorv", "Hadr",
        "Ilvan", "Jexor", "Kelvr", "Morv", "Nerith", "Pyrel", "Quilan", "Sarv", "Tulv", "Umbr",
        "Vexil", "Wrent", "Xandr", "Zorb", "Aldrav", "Belv", "Corvan", "Drav", "Elvon", "Farn"]
_SUF = ["ex", "ilo", "ix", "a", "ika", "ora", "an", "ium", "en", "ina", "al", "aro", "el", "on", "oxa"]
ROOTS = sorted({p + s for p in _PRE for s in _SUF})
TRADES = ["Lifesciences", "Formulations", "Remedies", "Pharmaceuticals", "Healthcare",
          "Laboratories", "Biotech", "Parenterals", "Drugs", "Medicare"]

# town, district, state, short form, pin, weight (Baddi-Solan is the big hub)
LOCATIONS = [
    ("Baddi", "Solan", "Himachal Pradesh", "H.P.", "173205", 9),
    ("Nalagarh", "Solan", "Himachal Pradesh", "H.P.", "174101", 3),
    ("Paonta Sahib", "Sirmaur", "Himachal Pradesh", "H.P.", "173025", 3),
    ("Kala Amb", "Sirmaur", "Himachal Pradesh", "H.P.", "173030", 2),
    ("Haridwar", "Haridwar", "Uttarakhand", "U.K.", "249403", 4),
    ("Roorkee", "Haridwar", "Uttarakhand", "U.K.", "247667", 2),
    ("Selaqui", "Dehradun", "Uttarakhand", "U.K.", "248011", 2),
    ("Bahadurgarh", "Jhajjar", "Haryana", "HR", "124507", 2),
    ("Panchkula", "Panchkula", "Haryana", "HR", "134113", 1),
    ("Vatva", "Ahmedabad", "Gujarat", "Gujarat", "382445", 3),
    ("Vadodara", "Vadodara", "Gujarat", "Gujarat", "390010", 2),
    ("Pithampur", "Dhar", "Madhya Pradesh", "M.P.", "454775", 2),
    ("Indore", "Indore", "Madhya Pradesh", "M.P.", "452010", 1),
    ("Namthang", "Namchi", "Sikkim", "Sikkim", "737137", 1),
    ("Verna", "South Goa", "Goa", "Goa", "403722", 1),
    ("Jeedimetla", "Medchal", "Telangana", "Telangana", "500055", 2),
    ("Pimpri", "Pune", "Maharashtra", "Maharashtra", "411018", 1),
    ("Patna", "Patna", "Bihar", "Bihar", "800013", 1),
    ("Kolkata", "Kolkata", "West Bengal", "W.B.", "700088", 1),
    ("Ghaziabad", "Ghaziabad", "Uttar Pradesh", "U.P.", "201001", 1),
]

# generic name, strength, form, fictional brand stem (None = sold unbranded)
DRUGS = [
    ("Paracetamol Tablets IP", "500 mg", "tablet", "Feverol"),
    ("Pantoprazole Gastro-Resistant Tablets IP", "40 mg", "tablet", "Pantovex"),
    ("Amoxycillin and Potassium Clavulanate Tablets IP", "625 mg", "tablet", "Clavimox"),
    ("Metformin Hydrochloride Tablets IP", "500 mg", "tablet", "Glucofine"),
    ("Calcium and Vitamin D3 Tablets IP", "500 mg", "tablet", "Calvita"),
    ("Cefixime Tablets IP", "200 mg", "tablet", "Cefinova"),
    ("Azithromycin Tablets IP", "500 mg", "tablet", "Azivex"),
    ("Albendazole Tablets IP", "400 mg", "tablet", None),
    ("Ibuprofen Tablets IP", "400 mg", "tablet", "Ibufast"),
    ("Aceclofenac and Paracetamol Tablets", "100 mg/325 mg", "tablet", "Acepar"),
    ("Telmisartan Tablets IP", "40 mg", "tablet", "Telmora"),
    ("Amlodipine Tablets IP", "5 mg", "tablet", None),
    ("Ondansetron Injection IP", "2 mg/ml", "injection", "Ondaset"),
    ("Dextrose Injection IP", "5% w/v", "injection", None),
    ("Ceftriaxone for Injection IP", "1 g", "injection", "Ceftrix"),
    ("Methylcobalamin Injection", "500 mcg", "injection", "Methycal"),
    ("Dextromethorphan Hydrobromide Syrup", "10 mg/5 ml", "syrup", "Coughnil"),
    ("Ciprofloxacin Tablets IP", "500 mg", "tablet", "Ciprodex"),
    ("Glimepiride Tablets IP", "2 mg", "tablet", None),
    ("Carboxymethylcellulose Sodium Eye Drops IP", "0.5% w/v", "eye drops", "Tearsoft"),
    ("Omeprazole Capsules IP", "20 mg", "capsule", "Omevex"),
    ("Levocetirizine Tablets IP", "5 mg", "tablet", None),
]

REASONS = {
    "tablet": [("Assay", 30), ("Dissolution", 30), ("Description", 10), ("Disintegration", 8),
               ("Uniformity of weight", 6), ("Identification", 5), ("Related substances", 4)],
    "capsule": [("Assay", 35), ("Dissolution", 35), ("Description", 15), ("Uniformity of weight", 15)],
    "injection": [("Sterility", 25), ("Particulate matter", 25), ("Bacterial endotoxins", 15),
                  ("Assay", 20), ("pH", 15)],
    "syrup": [("Assay", 50), ("pH", 25), ("Microbial contamination", 25)],
    "eye drops": [("Sterility", 50), ("pH", 30), ("Assay", 20)],
}

CENTRAL_LABS = ["CDL, Kolkata", "CDTL, Mumbai", "CDTL, Chennai", "CDTL, Hyderabad",
                "RDTL, Guwahati", "RDTL, Chandigarh", "CDL, Kasauli"]
STATES_REPORTING = ["Karnataka", "Kerala", "Punjab", "Telangana", "Maharashtra",
                    "Himachal Pradesh", "Tamil Nadu", "Gujarat", "West Bengal", "Rajasthan",
                    "Delhi", "Uttar Pradesh", "Andhra Pradesh", "Haryana", "Bihar", "Odisha"]

# Invented "top brands" that get faked. Each belongs to one (invented) owner,
# so only fakes and the owner's own failures match them.
QR_BRANDS = [("Febrinex 650", "Paracetamol Tablets IP", "650 mg"),
             ("Calsorin 500", "Calcium and Vitamin D3 Tablets IP", "500 mg"),
             ("Amclavo 625", "Amoxycillin and Potassium Clavulanate Tablets IP", "625 mg"),
             ("Metgluco 500", "Metformin Hydrochloride Tablets IP", "500 mg")]

STAMP = "SAMPLE DATA - NOT REAL. Invented companies and brands for testing only."


# ----------------------------------------------------------------- the world

def _make_world(rng: random.Random, n_companies: int = 170) -> pd.DataFrame:
    """Companies, their sites (factories), noise variants and risk levels."""
    roots = rng.sample(ROOTS, n_companies)
    loc_weights = [l[5] for l in LOCATIONS]
    sites = []
    for ci, root in enumerate(roots):
        trade = rng.choice(TRADES)
        legal = rng.choice(["Pvt. Ltd.", "Pvt. Ltd.", "Ltd.", "Private Limited", ""])
        name = f"{root} {trade} {legal}".strip()
        n_sites = 2 if rng.random() < 0.22 else 1
        locs = rng.sample(range(len(LOCATIONS)), k=n_sites) if n_sites > 1 else \
            [rng.choices(range(len(LOCATIONS)), weights=loc_weights)[0]]
        for si, li in enumerate(locs):
            town, district, state, short, pin, _ = LOCATIONS[li]
            plot = rng.choice([f"Plot No. {rng.randint(1, 240)}", f"Khasra No. {rng.randint(10, 400)}/{rng.randint(1, 9)}",
                               f"Survey No. {rng.randint(10, 300)}", f"Village {rng.choice(['Kishanpura', 'Lodhimajra', 'Bhatoli Kalan', 'Sandholi', 'Malpur'])}"])
            sites.append({
                "company_id": f"C{ci:03d}", "site_id": f"C{ci:03d}-S{si}", "company_name": name,
                "root": root, "trade": trade, "legal": legal,
                "town": town, "district": district, "state": state, "short": short, "pin": pin,
                "plot": plot,
                # Most sites fail rarely; a few fail often (the repeat offenders).
                "base_risk": min(rng.lognormvariate(-0.6, 1.0), 4.0),
                "start_t": rng.choice([0] * 8 + list(range(6, 30))),
                "stop_t": rng.choice([999] * 7 + list(range(20, 50))),
                "products": rng.sample(range(len(DRUGS)), k=rng.randint(4, 9)),
            })
    return pd.DataFrame(sites)


def _name_variant(site: pd.Series, rng: random.Random) -> str:
    """The same factory written the many ways CDSCO lists actually write it."""
    root, trade = site["root"], site["trade"]
    trade_v = {"Lifesciences": ["Lifesciences", "Life Sciences", "Lifescience", "Lifesceinces"],
               "Pharmaceuticals": ["Pharmaceuticals", "Pharmaceutical", "Pharma", "Pharmaceuticals"],
               "Laboratories": ["Laboratories", "Labs", "Laboratories", "Laboratory"],
               "Healthcare": ["Healthcare", "Health Care", "Healthcare"],
               }.get(trade, [trade])
    legal_v = {"Pvt. Ltd.": ["Pvt. Ltd.", "Pvt Ltd", "Private Limited", "(P) Ltd.", "Pvt. Ltd"],
               "Private Limited": ["Private Limited", "Pvt. Ltd.", "Pvt Ltd"],
               "Ltd.": ["Ltd.", "Limited", "Ltd"],
               "": ["", ""]}[site["legal"]]
    r = root
    if rng.random() < 0.06 and len(r) > 5:  # an occasional typo
        i = rng.randint(2, len(r) - 2)
        r = r[:i] + r[i + 1:]
    name = f"{r} {rng.choice(trade_v)} {rng.choice(legal_v)}".strip()
    prefix = rng.choice(["M/s. ", "M/s ", "M/S. ", "", "M/s. "])
    town, district, state, short, pin = site["town"], site["district"], site["state"], site["short"], site["pin"]
    addr = rng.choice([
        f"{site['plot']}, {town}, Distt. {district} ({short}) {pin}",
        f"{site['plot']}, Industrial Area, {town}, District {district}, {state}-{pin}",
        f"{town}-{pin} ({short}) India",
        f"{site['plot']}, {town}, {district}, {state} - {pin}, India",
        f"{town}, Distt-{district}-{pin}",
    ])
    return f"{prefix}{name}, {addr}"


def _months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out, (y, m) = [], start
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _simulate(world: pd.DataFrame, months: list[tuple[int, int]], rng: random.Random,
              nrng: np.random.Generator) -> pd.DataFrame:
    """Decide which sites get flagged each month. Recent failures raise the
    chance of failing again, and busy districts drag their neighbours up."""
    recent = {s: [] for s in world["site_id"]}
    site_district = dict(zip(world["site_id"], world["district"]))
    rows = []
    batch_seq = 10000
    for t, (y, m) in enumerate(months):
        era_new = y >= 2024
        target = nrng.poisson(70 if era_new else 30) + (12 if y >= 2025 else 0)
        district_heat = {}
        for s, hist in recent.items():
            d = site_district[s]
            district_heat[d] = district_heat.get(d, 0) + sum(1 for h in hist if t - h <= 3)
        weights = []
        for _, site in world.iterrows():
            active = site["start_t"] <= t < site["stop_t"]
            if not active:
                weights.append(0.0)
                continue
            hist = recent[site["site_id"]]
            r3 = min(sum(1 for h in hist if t - h <= 3), 3)
            r12 = min(sum(1 for h in hist if 3 < t - h <= 12), 4)
            self_excite = 1 + 0.6 * r3 + 0.2 * r12
            heat = 1 + 0.03 * min(district_heat.get(site["district"], 0), 20)
            weights.append(site["base_risk"] * self_excite * heat)
        w = np.array(weights)
        if w.sum() == 0:
            continue
        picks = nrng.choice(len(world), size=target, p=w / w.sum())
        for idx in picks:
            site = world.iloc[idx]
            recent[site["site_id"]].append(t)
            g, strength, form, brand = DRUGS[rng.choice(site["products"])]
            mfg_back = rng.randint(1, 14)
            mm_ = (y * 12 + m - 1) - mfg_back
            mfg = (mm_ // 12, mm_ % 12 + 1)
            ex = mm_ + rng.choice([18, 24, 24, 36])
            exp = (ex // 12, ex % 12 + 1)
            reason = rng.choices([r for r, _ in REASONS[form]], weights=[w_ for _, w_ in REASONS[form]])[0]
            if rng.random() < 0.15:
                reason += f" and {rng.choice([r for r, _ in REASONS[form] if r != reason])}"
            batch_seq += 1
            prefix = "".join(c for c in site["root"][:2].upper())
            central = rng.random() < (0.40 if era_new else 0.55)
            rows.append({
                "t": t, "year": y, "month": m, "site_id": site["site_id"],
                "company_id": site["company_id"],
                "generic": g, "strength": strength, "form": form,
                "brand": f"{brand}-{strength.split()[0]}" if brand and rng.random() < 0.7 else "",
                "batch_no": f"{prefix}{rng.choice(['', '-', '/'])}{mfg[0] % 100:02d}{batch_seq}",
                "mfg": mfg, "exp": exp, "reason": reason,
                "manufacturer_text": _name_variant(site, rng),
                "central": central,
                "lab": rng.choice(CENTRAL_LABS) if central else "",
                "state_lab": "" if central else rng.choice(STATES_REPORTING),
                "spurious": False,
            })
        # Spurious batches: fakes of big QR-coded brands, made by unknown people.
        n_fake = nrng.poisson(2.2 if y >= 2024 else 1.0)
        for _ in range(n_fake):
            qbrand, qgen, qstr = rng.choice(QR_BRANDS)
            batch_seq += 1
            mm_ = (y * 12 + m - 1) - rng.randint(1, 10)
            rows.append({
                "t": t, "year": y, "month": m, "site_id": "UNKNOWN", "company_id": "UNKNOWN",
                "generic": qgen, "strength": qstr, "form": "tablet", "brand": qbrand,
                "batch_no": f"FK{batch_seq}", "mfg": (mm_ // 12, mm_ % 12 + 1),
                "exp": ((mm_ + 24) // 12, (mm_ + 24) % 12 + 1),
                "reason": "Spurious - the firm whose name is on the label has disowned this batch",
                "manufacturer_text": "M/s. Rovenza Healthcare Ltd. (as claimed on label), Andheri East, Mumbai-400069",
                "central": rng.random() < 0.5, "lab": rng.choice(CENTRAL_LABS),
                "state_lab": rng.choice(STATES_REPORTING), "spurious": True,
            })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- PDF writers

_styles = getSampleStyleSheet()
_cell = ParagraphStyle("cell", parent=_styles["Normal"], fontSize=7, leading=8.5)
_head = ParagraphStyle("head", parent=_cell, fontName="Helvetica-Bold")
_title = ParagraphStyle("title", parent=_styles["Title"], fontSize=12, leading=14)
_note = ParagraphStyle("note", parent=_styles["Normal"], fontSize=8, leading=10)


def _P(text: str, style=_cell) -> Paragraph:
    return Paragraph(str(text).replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>"), style)


def _table(header: list[str], body: list[list[str]], widths: list[float]) -> Table:
    data = [[_P(h, _head) for h in header]] + [[_P(c) for c in r] for r in body]
    tbl = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
    ]))
    return tbl


def _fmt(ym: tuple[int, int], style: str) -> str:
    y, m = ym
    if style == "slash":
        return f"{m:02d}/{y}"
    if style == "mon":
        return f"{MONTH_NAMES[m - 1][:3]}-{y % 100:02d}"
    return f"{MONTH_NAMES[m - 1][:3]} {y}"


def _drug_label(r: pd.Series) -> str:
    base = f"{r['generic']} {r['strength']}"
    return f"{base} ({r['brand']})" if r["brand"] else base


def _write_old(path: Path, y: int, m: int, rows: pd.DataFrame, rng: random.Random) -> int:
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm,
                            rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    nsq = int((~rows["spurious"]).sum())
    spur = int(rows["spurious"].sum())
    tested = nsq + spur + rng.randint(900, 1400)
    story = [
        Paragraph(STAMP, _note),
        Paragraph("List of Drugs, Medical Devices and Cosmetics declared as Not of Standard "
                  f"Quality/Spurious/Adulterated/Misbranded for the Month of {MONTH_NAMES[m - 1]} - {y}", _title),
        Paragraph(f"Total number of samples tested {tested}", _note),
        Paragraph(f"Total number of samples declared as of Standard Quality {tested - nsq - spur}", _note),
        Paragraph(f"Total number of samples declared as Not of Standard Quality {nsq}", _note),
        Paragraph(f"Total number of samples declared as Spurious {spur if spur else '-'}", _note),
        Paragraph("Total number of samples declared as Misbranded -", _note),
        Spacer(1, 4 * mm),
    ]
    header = ["S.No.", "Name of Drugs/medical device/cosmetics",
              "Batch No./Date of Manufacture/Date of Expiry/Manufactured By",
              "Reason for failure", "Drawn By", "From"]
    body = []
    split_done = False
    for i, (_, r) in enumerate(rows.iterrows(), start=1):
        mfd = r["manufacturer_text"]
        combined = (f"B. No.: {r['batch_no']}\nMfg dt: {_fmt(r['mfg'], 'slash')}\n"
                    f"Exp dt: {_fmt(r['exp'], 'slash')}\nMfd by: {mfd}")
        drawn = (f"CDSCO Sub Zone {rng.choice(['Guwahati', 'Baddi', 'Indore', 'Hyderabad'])}"
                 if r["central"] else f"Drug Control Department, {r['state_lab']}")
        reason = r["reason"]
        if not split_done and "," in mfd and rng.random() < 0.3:
            # Imitate a row whose address ran on into a second row.
            head, tail = mfd.split(",", 1)
            combined = combined.replace(mfd, head + ",")
            body.append([str(i), _drug_label(r), combined, reason, drawn, r["lab"] or "RDTL, Guwahati"])
            body.append(["", "", tail.strip(), "", "", ""])
            split_done = True
            continue
        body.append([str(i), _drug_label(r), combined, reason, drawn, r["lab"] or "RDTL, Guwahati"])
    story.append(_table(header, body, [12, 55, 80, 45, 40, 30]))
    doc.build(story)
    return len(rows)


def _write_new(path: Path, kind: str, y: int, m: int, rows: pd.DataFrame) -> int:
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=10 * mm,
                            rightMargin=10 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    mon = f"{MONTH_NAMES[m - 1].upper()}-{y}"
    if kind == "central":
        title = f"A. NOT OF STANDARD QUALITY (NSQ) ALERT FOR THE MONTH OF {mon} (CDSCO/Central Laboratories)"
        last = "Reported by CDSCO Laboratory"
        style = "slash"
    elif kind == "state":
        title = f"B. NOT OF STANDARD QUALITY ALERT FOR THE MONTH OF {mon} (STATE LABORATORIES)"
        last = "Reported by State Laboratory"
        style = "mon"
    else:
        title = f"List of Drugs, Medical Devices, Vaccine and Cosmetics declared as Spurious for the Month of {mon}"
        last = "Reported by"
        style = "slash"
    story = [Paragraph(STAMP, _note), Paragraph(title, _title),
             Paragraph("As part of the continuous regulatory surveillance, drug samples are picked from "
                       "sales/distribution point, analyzed and list of Not of Standard Quality (NSQ) drugs "
                       "are displayed on CDSCO portal on monthly basis.", _note), Spacer(1, 3 * mm)]
    header = ["S.No", "Product/Drug Name", "Batch No.", "Manufacturing Date", "Expiry Date",
              "Manufactured By", "NSQ Result" if kind != "spurious" else "Remarks", last]
    body = []
    for i, (_, r) in enumerate(rows.iterrows(), start=1):
        rep = r["lab"] if kind == "central" else (r["state_lab"] if kind == "state" else
                                                 (r["lab"] if r["central"] else r["state_lab"]))
        body.append([str(i), _drug_label(r), r["batch_no"], _fmt(r["mfg"], style), _fmt(r["exp"], style),
                     r["manufacturer_text"], r["reason"], rep])
    story.append(_table(header, body, [10, 52, 22, 20, 18, 80, 40, 30]))
    doc.build(story)
    return len(rows)


# ----------------------------------------------------------------- portal JSON

def _portal_records(y: int, m: int, rows: pd.DataFrame, rng: random.Random) -> dict:
    recs = []
    for i, (_, r) in enumerate(rows.iterrows(), start=1):
        recs.append({
            "slNo": i,
            "drugName": _drug_label(r),
            "batchNumber": r["batch_no"],
            "dateOfManufacture": _fmt(r["mfg"], "slash"),
            "dateOfExpiry": _fmt(r["exp"], "slash"),
            "manufacturerName": r["manufacturer_text"],
            "nsqResult": r["reason"],
            "reportingSource": "CDSCO" if r["central"] else "State",
            "reportedBy": r["lab"] if r["central"] else r["state_lab"],
            "category": "Spurious" if r["spurious"] else "NSQ",
            "alertMonth": MONTH_NAMES[m - 1][:3].upper(),
            "alertYear": y,
        })
    pending = sorted(rng.sample(STATES_REPORTING, k=rng.randint(2, 6)))
    return {"data": recs, "pendingStates": pending}


# ----------------------------------------------------------------- main

def make_sample(paths: config.Paths, seed: int = 7) -> dict:
    """Write the whole sample dataset into ``paths``. Returns a small summary."""
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    paths.ensure()
    paths.dataset_label.write_text("SAMPLE - invented companies and brands, generated by "
                                   "`python -m nsq demo`. Not real CDSCO data.\n", encoding="utf-8")
    world = _make_world(rng)
    months = _months((2022, 1), (2026, 8))
    sim = _simulate(world, months, rng, nrng)

    index_rows, manifest_rows = [], []
    num_id = 20000

    def add(file_name: str, title: str, doc_type: str, y: int, m: int, revised: bool = False):
        nonlocal num_id
        num_id += 1
        ry, rm = (y + 1, 1) if m == 12 else (y, m + 1)
        rel = dt.date(ry, rm, 20).isoformat()
        rec = {"sno": "", "title": title, "release_date": rel,
               "url": f"https://example.invalid/sample?num_id={num_id}", "size_text": "",
               "num_id": num_id, "doc_type": doc_type, "report_year": y, "report_month": m,
               "is_revised": revised, "date_suspect": False}
        index_rows.append(rec)
        manifest_rows.append(rec | {"local_file": file_name, "status": "sample"})

    for (y, m), grp in sim.groupby(["year", "month"]):
        y, m = int(y), int(m)
        stem = f"{y}-{m:02d}"
        if y <= 2023:
            name = f"{stem}_combined_{num_id + 1}.pdf"
            _write_old(paths.pdfs / name, y, m, grp, rng)
            add(name, f"Drug Alert list for month of {MONTH_NAMES[m - 1]} {y}", "combined", y, m)
        elif (y, m) <= (2025, 7):
            cen = grp[grp["central"] & ~grp["spurious"]]
            sta = grp[~grp["central"] & ~grp["spurious"]]
            spu = grp[grp["spurious"]]
            name = f"{stem}_central_nsq_{num_id + 1}.pdf"
            _write_new(paths.pdfs / name, "central", y, m, cen)
            add(name, f"NSQ ALERT FOR THE MONTH OF {MONTH_NAMES[m - 1].upper()}-{y}", "central_nsq", y, m)
            name = f"{stem}_state_nsq_{num_id + 1}.pdf"
            _write_new(paths.pdfs / name, "state", y, m, sta)
            add(name, f"STATE NSQ ALERT FOR THE MONTH OF {MONTH_NAMES[m - 1].upper()}-{y}", "state_nsq", y, m)
            if len(spu):
                name = f"{stem}_spurious_{num_id + 1}.pdf"
                _write_new(paths.pdfs / name, "spurious", y, m, spu)
                add(name, f"List of spurious Drugs for the month of {MONTH_NAMES[m - 1]}-{y}", "spurious", y, m)
            if (y, m) == (2024, 11):
                # A revised central list: same rows, one batch number corrected.
                fixed = cen.copy()
                if len(fixed):
                    fixed.iloc[0, fixed.columns.get_loc("batch_no")] = fixed.iloc[0]["batch_no"] + "R"
                name = f"{stem}_central_nsq_rev_{num_id + 1}.pdf"
                _write_new(paths.pdfs / name, "central", y, m, fixed)
                add(name, f"Revise list drug alert {MONTH_NAMES[m - 1]}-{y}", "central_nsq", y, m, revised=True)
                sim.loc[sim["batch_no"] == cen.iloc[0]["batch_no"], "batch_no_revised"] = fixed.iloc[0]["batch_no"]
        else:
            payload = _portal_records(y, m, grp, rng)
            (paths.portal / f"portal_{stem}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")

    pd.DataFrame(index_rows).to_csv(paths.alert_index, index=False)
    pd.DataFrame(manifest_rows).to_csv(paths.manifest, index=False)

    truth = sim[["batch_no", "site_id", "company_id", "year", "month", "generic", "spurious"]].copy()
    if "batch_no_revised" in sim.columns:
        extra = sim.dropna(subset=["batch_no_revised"])[["batch_no_revised", "site_id", "company_id",
                                                          "year", "month", "generic", "spurious"]]
        truth = pd.concat([truth, extra.rename(columns={"batch_no_revised": "batch_no"})])
    truth.to_csv(paths.reference / "sample_truth.csv", index=False)
    world.drop(columns=["products"]).to_csv(paths.reference / "sample_sites.csv", index=False)
    pd.DataFrame([{"brand": b, "generic": g, "strength": s, "source": "invented for the demo"}
                  for b, g, s in QR_BRANDS]).to_csv(paths.reference / "qr_brands.csv", index=False)
    return {"months": len(months), "rows": len(sim), "sites": len(world),
            "pdfs": len(manifest_rows), "portal_months": len(list(paths.portal.glob("*.json")))}
