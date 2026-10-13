"""Stage 6: data quality checks. Each one says PASS, WARN or FAIL and why.

Run with --strict in the monthly job so a broken month stops the pipeline
instead of quietly reaching the dashboard.
"""
from __future__ import annotations

import logging

import pandas as pd

from . import config
from .warehouse import query

log = logging.getLogger(__name__)


def _row(check, status, value, detail):
    return {"check": check, "status": status, "value": value, "detail": detail}


def run_checks(paths: config.Paths) -> pd.DataFrame:
    out = []
    summary = pd.read_csv(paths.doc_summary)
    with_totals = summary[summary["count_check"].isin(["ok", "mismatch"])]
    if len(with_totals):
        bad = with_totals[with_totals["count_check"] == "mismatch"]
        share = len(bad) / len(with_totals)
        out.append(_row("rows match stated totals", "PASS" if share == 0 else "WARN" if share < 0.1 else "FAIL",
                        f"{len(with_totals) - len(bad)}/{len(with_totals)} files",
                        "files: " + ", ".join(bad["source_file"].head(5)) if len(bad) else
                        "every list with stated totals parsed completely"))
    errors = summary[summary["count_check"].astype(str).str.startswith("error")]
    out.append(_row("files parsed without errors", "PASS" if errors.empty else "FAIL",
                    f"{len(summary) - len(errors)}/{len(summary)}",
                    ", ".join(errors["source_file"].head(5)) if len(errors) else ""))
    no_table_pages = summary["pages_without_tables"].fillna("").astype(str).str.len().gt(0).sum() \
        if "pages_without_tables" in summary else 0
    out.append(_row("pages with rows but no table found", "PASS" if no_table_pages == 0 else "WARN",
                    int(no_table_pages), "re-run with --llm to read these pages" if no_table_pages else ""))

    f = query(paths, "SELECT * FROM fct_flags")
    n = len(f)
    for col, label in (("batch_no", "batch number"), ("manufacturer_raw", "manufacturer"),
                       ("drug_name", "drug name")):
        miss = (f[col].fillna("") == "").mean() if n else 0
        out.append(_row(f"rows with a {label}", "PASS" if miss < 0.02 else "WARN" if miss < 0.05 else "FAIL",
                        f"{1 - miss:.1%}", ""))
    real = f[~f["is_spurious"]]
    unlinked = real["site_id"].isna().mean() if len(real) else 0
    out.append(_row("non-spurious rows linked to a site", "PASS" if unlinked < 0.02 else "FAIL",
                    f"{1 - unlinked:.1%}", ""))
    bad_dates = ((f["mfg_month"].notna()) & (f["exp_month"].notna()) & (f["mfg_month"] > f["exp_month"])).sum()
    out.append(_row("manufacture date before expiry", "PASS" if bad_dates == 0 else "WARN",
                    f"{n - bad_dates}/{n}", f"{bad_dates} rows have mfg after expiry" if bad_dates else ""))
    pin_only = (real["mfr_state_source"] == "pin").mean() if len(real) else 0
    out.append(_row("states from address text or district (not PIN alone)",
                    "PASS" if pin_only < 0.2 else "WARN", f"{1 - pin_only:.1%}",
                    "a state taken from the PIN code alone is approximate"))

    months = pd.to_datetime(f["report_month"]).dt.to_period("M")
    if len(months):
        full = pd.period_range(months.min(), months.max(), freq="M")
        gaps = sorted(set(full) - set(months))
        out.append(_row("months with at least one alert", "PASS" if not gaps else "WARN",
                        f"{len(full) - len(gaps)}/{len(full)}",
                        "missing: " + ", ".join(str(g) for g in gaps[:12]) if gaps else ""))

    llm_rows = (f["parse_method"] == "llm").sum()
    out.append(_row("rows read by the LLM fallback", "PASS", int(llm_rows),
                    "each is logged and checked against the page text" if llm_rows else "none"))

    truth_path = paths.reference / "sample_truth.csv"
    if truth_path.exists():
        from .entities import bcubed
        truth = pd.read_csv(truth_path)
        truth = truth[truth["site_id"] != "UNKNOWN"].drop_duplicates("batch_no")
        j = real.merge(truth, on="batch_no", how="inner", suffixes=("", "_true"))
        if len(j):
            comp = bcubed(j["company_id"].fillna("none"), j["company_id_true"])
            site = bcubed(j["site_id"].fillna("none"), j["site_id_true"])
            out.append(_row("entity matching vs answer key (company, B-cubed F1)",
                            "PASS" if comp["bcubed_f1"] >= 0.95 else "WARN", comp["bcubed_f1"],
                            f"precision {comp['bcubed_precision']}, recall {comp['bcubed_recall']}, "
                            f"{comp['rows']} rows"))
            out.append(_row("entity matching vs answer key (site, B-cubed F1)",
                            "PASS" if site["bcubed_f1"] >= 0.95 else "WARN", site["bcubed_f1"],
                            f"precision {site['bcubed_precision']}, recall {site['bcubed_recall']}"))

    res = pd.DataFrame(out)
    paths.exports.mkdir(parents=True, exist_ok=True)
    res.to_csv(paths.exports / "quality_report.csv", index=False)
    return res
