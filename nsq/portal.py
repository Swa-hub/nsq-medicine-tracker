"""Stage 1c: read alerts from CDSCO's NSQ portal (Aug 2025 onward).

From August 2025, CDSCO publishes monthly alerts in a web app
(https://cdscoonline.gov.in/CDSCO/viewPublicNSQDrug) instead of PDFs. The app
loads its table from a data request in the background. Its exact address and
field names are not documented, so this module works from what you capture:

1. Open the portal in Chrome, press F12, open the Network tab, tick "Fetch/XHR".
2. Pick a year and month, click "Apply Filter".
3. Either right-click the request that returned the table -> "Copy response"
   and save it as data/raw/portal/portal_YYYY-MM.json,
   or click the download arrow ("Export HAR") and save the .har file into
   data/raw/portal/. Repeat for each month you need.

``load_portal`` then maps whatever the field names are onto the standard
columns, using the synonym lists below. If a column comes out empty, add the
portal's field name to the right list.

The portal also shows "Pending States" (states that have not reported yet).
If the saved JSON includes them, they are kept for the testing-gap view.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from . import config
from .parse_pdf import ROW_COLUMNS, _finish_row
from .textutil import MONTHS, norm_key

log = logging.getLogger(__name__)

SYNONYMS = {
    "sno": ["slno", "sno", "srno", "serialno", "sl"],
    "drug_name": ["drugname", "productname", "nameofdrug", "productdrugname", "drug", "product"],
    "batch_no": ["batchno", "batchnumber", "batch"],
    "mfg_raw": ["dateofmanufacture", "manufacturingdate", "mfgdate", "mfgdt", "mfddate"],
    "exp_raw": ["dateofexpiry", "expirydate", "expdate", "expdt"],
    "manufacturer_raw": ["manufacturername", "manufacturedby", "manufacturer", "manufacturerdetails",
                         "firmname", "mfdby"],
    "reason_raw": ["nsqresult", "result", "reasonforfailure", "reason", "remarks", "testresult"],
    "reported_by": ["reportedby", "laboratory", "labname", "reportinglab", "statename", "state"],
    "source_kind": ["reportingsource", "source", "reportedfrom", "labtype"],
    "category": ["category", "type", "alerttype", "nsqtype"],
    "month": ["alertmonth", "month", "reportmonth"],
    "year": ["alertyear", "year", "reportyear"],
}
PENDING_KEYS = ["pendingstates", "pendingstate", "statespending"]


def _canon(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _find_records(obj) -> tuple[list[dict], list[str]]:
    """Walk any JSON shape and return (list of record dicts, pending states)."""
    pending: list[str] = []
    best: list[dict] = []

    def walk(o):
        nonlocal best, pending
        if isinstance(o, dict):
            for k, v in o.items():
                if _canon(k) in PENDING_KEYS and isinstance(v, list):
                    pending = [str(x if not isinstance(x, dict) else next(iter(x.values()))) for x in v]
                walk(v)
        elif isinstance(o, list):
            if o and all(isinstance(x, dict) for x in o) and len(o) > len(best):
                fields = {_canon(k) for k in o[0]}
                if any(s in fields for s in SYNONYMS["batch_no"]):
                    best = o
            for x in o:
                walk(x)

    walk(obj)
    return best, pending


def _har_payloads(path: Path) -> list:
    har = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for entry in har.get("log", {}).get("entries", []):
        content = entry.get("response", {}).get("content", {})
        text = content.get("text")
        if not text or "json" not in content.get("mimeType", "") and not text.strip().startswith(("{", "[")):
            continue
        try:
            out.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return out


def map_record(rec: dict) -> dict:
    canon = {_canon(k): v for k, v in rec.items()}
    out = {}
    for field, names in SYNONYMS.items():
        for n in names:
            if n in canon and canon[n] not in (None, ""):
                out[field] = str(canon[n])
                break
    return out


def load_portal(paths: config.Paths) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read every .json / .har in raw/portal and return (rows, pending states)."""
    rows, pending_rows = [], []
    files = sorted(list(paths.portal.glob("*.json")) + list(paths.portal.glob("*.har")))
    for f in files:
        payloads = _har_payloads(f) if f.suffix == ".har" else [json.loads(f.read_text(encoding="utf-8"))]
        file_month = re.search(r"(20\d\d)-(\d\d)", f.name)
        for payload in payloads:
            records, pending = _find_records(payload)
            for i, r in enumerate(records, start=1):
                m = map_record(r)
                year = int(m["year"]) if m.get("year", "").isdigit() else (
                    int(file_month.group(1)) if file_month else None)
                mon_raw = m.get("month", "")
                month = (int(mon_raw) if mon_raw.isdigit() else MONTHS.get(norm_key(mon_raw)[:3])) \
                    if mon_raw else (int(file_month.group(2)) if file_month else None)
                kind = norm_key(m.get("source_kind", ""))
                cat = norm_key(m.get("category", ""))
                section = "spurious" if "spurious" in cat else (
                    "state" if "state" in kind else "central" if kind else "unknown")
                rec = {k: m.get(k, "") for k in ("sno", "drug_name", "batch_no", "mfg_raw", "exp_raw",
                                                    "manufacturer_raw", "reason_raw", "reported_by")}
                rec.update(drawn_by="", page=None, row_idx=i, section=section, parse_method="portal")
                meta = {"source_file": f.name, "num_id": None,
                        "doc_type": "portal", "report_year": year, "report_month": month,
                        "is_revised": False, "release_date": None}
                _finish_row(rec, meta)
                if "spurious" in cat:
                    rec["is_spurious"], rec["section"] = True, "spurious"
                rows.append(rec)
            if pending and file_month:
                for s in pending:
                    pending_rows.append({"report_year": int(file_month.group(1)),
                                         "report_month": int(file_month.group(2)), "state": s,
                                         "source_file": f.name})
    df = pd.DataFrame(rows)
    for c in ROW_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[ROW_COLUMNS] if not df.empty else pd.DataFrame(columns=ROW_COLUMNS)
    pend = pd.DataFrame(pending_rows, columns=["report_year", "report_month", "state", "source_file"])
    paths.interim.mkdir(parents=True, exist_ok=True)
    df.to_parquet(paths.interim / "portal_rows.parquet", index=False)
    pend.drop_duplicates(["report_year", "report_month", "state"]).to_csv(
        paths.interim / "portal_pending_states.csv", index=False)
    log.info("Portal: %d rows from %d files, %d pending-state entries", len(df), len(files), len(pend))
    if not df.empty:
        empty_cols = [c for c in ("drug_name", "batch_no", "manufacturer_raw") if (df[c] == "").all()]
        if empty_cols:
            log.warning("These columns came out empty: %s. Add the portal's field names to "
                        "SYNONYMS in nsq/portal.py.", empty_cols)
    return df, pend
