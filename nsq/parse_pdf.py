"""Stage 2: turn each alert PDF into rows with one standard set of columns.

CDSCO's format changed over the years:

* 2024 onward: separate columns
  S.No | Product/Drug Name | Batch No. | Manufacturing Date | Expiry Date |
  Manufactured By | NSQ Result | Reported by (lab or state)
* up to 2023: batch, dates and manufacturer squeezed into ONE cell
  S.No. | Name of Drugs | Batch No./Date of Manufacture/Date of Expiry/Manufactured By |
  Reason for failure | Drawn By | From
  and the first page states the month's totals (samples tested, NSQ, spurious).

Every output row keeps its source file, page and row number, so any number in
the dashboard can be traced back to the exact line of the PDF.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pdfplumber

from . import config
from .textutil import clean_ws, norm_key, parse_month_year

log = logging.getLogger(__name__)

ROW_COLUMNS = [
    "source_file", "num_id", "doc_type", "report_year", "report_month", "is_revised",
    "release_date", "page", "row_idx", "section", "sno", "drug_name", "batch_no",
    "mfg_raw", "exp_raw", "mfg_month", "exp_month", "manufacturer_raw", "reason_raw",
    "reported_by", "drawn_by", "is_spurious", "parse_method", "warnings",
]

# ---------------------------------------------------------------- headers

def map_header_cell(text: str) -> str | None:
    """Map one header cell to a standard field name (or None)."""
    h = norm_key(text)
    if not h:
        return None
    if "batch" in h and ("manufactured" in h or "mfd" in h or ("manufacture" in h and "expiry" in h)):
        return "combined_cell"
    if re.fullmatch(r"(s|sl|sr)\s?no|s\s?n|sno|s no|serial no|sl", h) or h.startswith("s no"):
        return "sno"
    if "batch" in h:
        return "batch_no"
    if "reported by" in h or "laboratory" in h or h in {"from", "lab", "tested by", "testing lab"}:
        return "reported_by"
    if "drawn" in h:
        return "drawn_by"
    if "manufactured by" in h or "manufacturer" in h or "mfd by" in h or "marketed by" in h:
        return "manufacturer_raw"
    if "expiry" in h or h in {"exp", "exp date", "exp dt"}:
        return "exp_raw"
    if ("manufactur" in h and "date" in h) or h.startswith("mfg") or "date of manufacture" in h:
        return "mfg_raw"
    if "reason" in h or "result" in h or "failure" in h or "test" in h or "remark" in h:
        return "reason_raw"
    if "name" in h or "product" in h or "drug" in h:
        return "drug_name"
    return None


def map_header(cells: list) -> dict[int, str] | None:
    # A data row starts with its serial number; a header row never does.
    first = clean_ws(cells[0] if cells else "")
    if re.fullmatch(r"\d+\.?", first):
        return None
    # Header cells are short labels, not sentences or addresses.
    if any(len(clean_ws(c or "")) > 80 for c in cells):
        return None
    mapping = {}
    for i, c in enumerate(cells):
        f = map_header_cell(c or "")
        if f and f not in mapping.values():
            mapping[i] = f
    fields = set(mapping.values())
    looks_like_header = len(fields) >= 3 and ("drug_name" in fields) and (
        "batch_no" in fields or "combined_cell" in fields)
    return mapping if looks_like_header else None


# ---------------------------------------------------------------- combined cell

_LABELS = re.compile(
    r"(?P<manufacturer>\b(?:Mfd|Mfg|Mkd)\.?\s*by\b|\bManufactured\s*by\b|\bMarketed\s*by\b)"
    r"|(?P<batch>\bB\.?\s*No\b\.?|\bBatch\s*No\b\.?)"
    r"|(?P<mfg>\b(?:Mfg|Mfd)\.?\s*(?:dt|date|d)\b\.?|\bDate\s*of\s*(?:Mfg|Manufacture)\b\.?|\bM\.\s*D\b\.?)"
    r"|(?P<exp>\bExp(?:iry)?\.?\s*(?:dt|date|d)?\b\.?|\bDate\s*of\s*Exp(?:iry)?\b\.?|\bE\.\s*D\b\.?)",
    flags=re.IGNORECASE,
)


def split_combined_cell(text: str) -> dict:
    """Split an old-format cell such as
    'B. No.: 28021831 Mfg dt: 04/2021 Exp dt: 02/2023 Mfd by: M/s ABC Pharma, Baddi'
    into batch, dates and manufacturer."""
    out = {"batch_no": "", "mfg_raw": "", "exp_raw": "", "manufacturer_raw": ""}
    if not text:
        return out
    s = str(text).replace("\r", "\n")
    hits = [(m.start(), m.end(), m.lastgroup) for m in _LABELS.finditer(s)]
    if not hits:
        # No labels: first line is usually the batch, an "M/s" starts the maker.
        lines = [l.strip() for l in s.split("\n") if l.strip()]
        if lines:
            out["batch_no"] = lines[0]
        ms = re.search(r"M/s\.?.*", s, flags=re.IGNORECASE | re.DOTALL)
        if ms:
            out["manufacturer_raw"] = clean_ws(ms.group(0))
        return out
    for i, (start, end, name) in enumerate(hits):
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(s)
        value = s[end:stop].strip(" :-\n\t")
        if name in ("mfg", "exp"):
            tok = re.match(r"[\w./\-' ]{2,12}", value)
            value = tok.group(0).strip() if tok else value
            value = value.split("\n")[0]
        elif name == "batch":
            value = value.split("\n")[0]
        if not out[{"batch": "batch_no", "mfg": "mfg_raw", "exp": "exp_raw",
                    "manufacturer": "manufacturer_raw"}[name]]:
            out[{"batch": "batch_no", "mfg": "mfg_raw", "exp": "exp_raw",
                 "manufacturer": "manufacturer_raw"}[name]] = clean_ws(value)
    if not out["manufacturer_raw"]:
        ms = re.search(r"M/s\.?.*", s, flags=re.IGNORECASE | re.DOTALL)
        if ms:
            out["manufacturer_raw"] = clean_ws(ms.group(0))
    return out


# ---------------------------------------------------------------- totals & sections

def _num(m: re.Match | None) -> int | None:
    if not m:
        return None
    v = m.group(1).strip()
    return 0 if v in {"-", "nil", "Nil", "NIL"} else int(v)


def stated_totals(text: str) -> dict:
    t = clean_ws(text)
    return {
        "stated_tested": _num(re.search(r"Total\s+number\s+of\s+samples\s+tested\s*[:\-]?\s*(\d+|-)", t, re.I)),
        "stated_nsq": _num(re.search(r"declared\s+as\s+Not\s+of\s+Standard\s+Quality\s*[:\-]?\s*(\d+|-|nil)", t, re.I)),
        "stated_spurious": _num(re.search(r"declared\s+as\s+Spurious\s*[:\-]?\s*(\d+|-|nil)", t, re.I)),
        "stated_misbranded": _num(re.search(r"declared\s+as\s+Misbranded\s*[:\-]?\s*(\d+|-|nil)", t, re.I)),
        "stated_adulterated": _num(re.search(r"declared\s+as\s+Adulterated\s*[:\-]?\s*(\d+|-|nil)", t, re.I)),
    }


_HEADINGS = (
    (r"state\s+laborator", "state"),
    (r"central\s+laborator", "central"),
    (r"cdsco\s*/\s*central", "central"),
    (r"spurious\s+drugs", "spurious"),
    # not the totals line "samples declared as Spurious -"
    (r"(?<!samples\s)declared\s+as\s+spurious", "spurious"),
)


def page_headings(page) -> list[tuple[float, str]]:
    """(top position, section) for every section heading on a page."""
    found = []
    for pat, name in _HEADINGS:
        try:
            for m in page.search(pat, regex=True, case=False):
                found.append((m["top"], name))
        except Exception:  # noqa: BLE001 - search is a nicety, not essential
            continue
    return sorted(found)


_DEFAULT_SECTION = {"central_nsq": "central", "state_nsq": "state", "spurious": "spurious",
                    "combined": "mixed", "compiled": "mixed"}


def infer_row_section(section: str, rec: dict) -> str:
    if section != "mixed":
        return section
    who = f"{rec.get('drawn_by', '')} {rec.get('reported_by', '')}".lower()
    if any(k in who for k in ("drug control", "state", "fda", "drugs control")):
        return "state"
    if any(k in who for k in ("cdsco", "cdl", "cdtl", "rdtl", "central", "ipc")):
        return "central"
    return "unknown"


# ---------------------------------------------------------------- main parser

@dataclass
class DocResult:
    rows: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    pages_without_tables: list = field(default_factory=list)
    page_texts: dict = field(default_factory=dict)


def parse_pdf(path: Path, meta: dict | None = None) -> DocResult:
    """Parse one alert PDF. ``meta`` carries index info (doc_type, month...)."""
    meta = dict(meta or {})
    doc_type = meta.get("doc_type", "combined")
    section = _DEFAULT_SECTION.get(doc_type, "mixed")
    res = DocResult()
    mapping: dict[int, str] | None = None
    ncols = None
    all_text = []

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            all_text.append(text)
            res.page_texts[pno] = text
            headings = page_headings(page)
            tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
            if not tables:
                if re.search(r"\n\s*\d+\s*[.)]?\s+\S", text):
                    res.pages_without_tables.append(pno)
                if headings:
                    section = headings[-1][1]
                continue
            for tbl in tables:
                # A table belongs to the last section heading above it.
                above = [h for h in headings if h[0] < tbl.bbox[1]]
                if above:
                    section = above[-1][1]
                table = tbl.extract()
                for ridx, raw_cells in enumerate(table):
                    cells = [clean_ws(c) if c is not None else "" for c in raw_cells]
                    raw_keep = [c if c is not None else "" for c in raw_cells]
                    if not any(cells):
                        continue
                    hdr = map_header(cells)
                    if hdr:
                        mapping, ncols = hdr, len(cells)
                        continue
                    if mapping is None or len(cells) != ncols:
                        # A table before any header (e.g. the totals box) or a
                        # different table shape: not a data row.
                        continue
                    rec = {f: cells[i] for i, f in mapping.items()}
                    old_format = "combined_cell" in mapping.values()
                    ci = next((i for i, f in mapping.items() if f == "combined_cell"), None)

                    # A row with no serial number and no batch is the tail of the
                    # previous row (a long address that ran onto the next page).
                    if old_format:
                        has_batch = bool(re.search(r"\bB\.?\s*No\b|\bBatch\b", raw_keep[ci], re.I))
                    else:
                        has_batch = bool(rec.get("batch_no"))
                    if not rec.get("sno") and not has_batch and res.rows:
                        prev = res.rows[-1]
                        for k, v in rec.items():
                            if not v:
                                continue
                            target = "manufacturer_raw" if k == "combined_cell" else k
                            prev[target] = clean_ws(f"{prev.get(target, '')} {v}")
                        continue

                    if old_format:
                        rec.update(split_combined_cell(raw_keep[ci]))
                        rec.pop("combined_cell", None)
                    rec["page"], rec["row_idx"] = pno, ridx
                    rec["section"] = infer_row_section(section, rec)
                    rec["parse_method"] = "table_old" if old_format else "table_new"
                    res.rows.append(rec)
            # A heading under the last table sets the section for the next page.
            below = [h for h in headings if h[0] > tables[-1].bbox[3]]
            if below:
                section = below[-1][1]

    full_text = "\n".join(all_text)
    totals = stated_totals(full_text)
    for rec in res.rows:
        _finish_row(rec, meta)
    parsed = len(res.rows)
    stated_all = [totals[k] for k in ("stated_nsq", "stated_spurious", "stated_misbranded",
                                      "stated_adulterated") if totals[k] is not None]
    expected = sum(stated_all) if stated_all else None
    res.summary = {
        "source_file": Path(path).name, **{k: meta.get(k) for k in ("num_id", "doc_type", "report_year",
                                                                     "report_month", "is_revised")},
        "pages": len(all_text), "rows_parsed": parsed,
        "rows_central": sum(r["section"] == "central" for r in res.rows),
        "rows_state": sum(r["section"] == "state" for r in res.rows),
        "rows_spurious": sum(bool(r["is_spurious"]) for r in res.rows),
        **totals,
        "expected_rows": expected,
        "count_check": ("no_totals" if expected is None else
                        "ok" if expected == parsed else "mismatch"),
        "pages_without_tables": ",".join(map(str, res.pages_without_tables)),
    }
    return res


def _finish_row(rec: dict, meta: dict) -> None:
    warnings = []
    for k in ("sno", "drug_name", "batch_no", "mfg_raw", "exp_raw", "manufacturer_raw",
              "reason_raw", "reported_by", "drawn_by"):
        rec.setdefault(k, "")
    rec["sno"] = re.sub(r"[^\d]", "", rec["sno"]) or ""
    rec["mfg_month"] = parse_month_year(rec["mfg_raw"])
    rec["exp_month"] = parse_month_year(rec["exp_raw"])
    if rec["mfg_raw"] and not rec["mfg_month"]:
        warnings.append("mfg_unparsed")
    if rec["exp_raw"] and not rec["exp_month"]:
        warnings.append("exp_unparsed")
    if not rec["batch_no"]:
        warnings.append("no_batch")
    if not rec["manufacturer_raw"]:
        warnings.append("no_manufacturer")
    reason = rec["reason_raw"].lower()
    rec["is_spurious"] = rec.get("section") == "spurious" or "spurious" in reason
    if rec["is_spurious"] and rec.get("section") in ("unknown", "mixed"):
        rec["section"] = "spurious"
    rec["warnings"] = ";".join(warnings)
    for k in ("num_id", "doc_type", "report_year", "report_month", "is_revised", "release_date"):
        rec[k] = meta.get(k)
    rec["source_file"] = meta.get("local_file") or meta.get("source_file")


# ---------------------------------------------------------------- whole folder

def parse_all(paths: config.Paths, use_llm: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse every PDF in the manifest. Writes nsq_rows.parquet and doc_summary.csv."""
    manifest = pd.read_csv(paths.manifest)
    ok = manifest[manifest["status"].isin(["downloaded", "cached", "sample"])]
    all_rows, summaries = [], []
    for _, m in ok.iterrows():
        pdf_path = paths.pdfs / m["local_file"]
        if not pdf_path.exists():
            log.warning("Missing file %s", pdf_path)
            continue
        meta = m.to_dict() | {"_interim": str(paths.interim)}
        try:
            res = parse_pdf(pdf_path, meta)
        except Exception as e:  # noqa: BLE001 - one bad PDF must not stop the run
            log.error("Could not parse %s: %s", m["local_file"], e)
            summaries.append({"source_file": m["local_file"], "count_check": f"error: {e}"})
            continue
        if use_llm and res.pages_without_tables:
            from .llm_fallback import extract_rows_from_pages
            extra = extract_rows_from_pages(res, meta)
            res.rows.extend(extra)
            res.summary["rows_llm"] = len(extra)
        all_rows.extend(res.rows)
        summaries.append(res.summary)
        log.info("%s: %d rows (%s)", m["local_file"], res.summary["rows_parsed"],
                 res.summary["count_check"])

    rows = pd.DataFrame(all_rows)
    for c in ROW_COLUMNS:
        if c not in rows.columns:
            rows[c] = None
    rows = rows[ROW_COLUMNS]
    rows = rows.astype({"is_spurious": bool})
    summary = pd.DataFrame(summaries)

    # Rows from the NSQ portal (Aug 2025 onward) join here, in the same columns.
    portal_rows = paths.interim / "portal_rows.parquet"
    if portal_rows.exists():
        pr = pd.read_parquet(portal_rows)
        rows = pd.concat([rows, pr[ROW_COLUMNS]], ignore_index=True)
        log.info("Added %d rows from the NSQ portal", len(pr))

    paths.interim.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(paths.rows, index=False)
    summary.to_csv(paths.doc_summary, index=False)
    return rows, summary
