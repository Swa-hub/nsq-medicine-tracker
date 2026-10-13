"""Write data/exports/findings.md: the headline numbers, each traceable to a table."""
from __future__ import annotations

import json

import pandas as pd

from . import config
from .warehouse import query


def _md(df: pd.DataFrame, n: int = 10) -> str:
    if df.empty:
        return "_none_\n"
    d = df.head(n).copy()
    for c in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%Y-%m")
    cols = list(d.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in d.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r.values) + " |")
    return "\n".join(lines) + "\n"


def write_report(paths: config.Paths):
    label = config.dataset_label(paths)
    cov = query(paths, """SELECT min(report_month) AS first_month, max(report_month) AS last_month,
                                 count(DISTINCT report_month) AS months, count(*) AS flags,
                                 sum(is_spurious::INT)::INT AS spurious,
                                 count(DISTINCT site_id) AS sites, count(DISTINCT company_id) AS companies,
                                 count(DISTINCT mfr_key) AS spellings
                          FROM fct_flags""").iloc[0]
    offenders = query(paths, """SELECT company_name, state, district, months_flagged, total_flags,
                                       first_flag_month, last_flag_month, spellings_seen
                                FROM mart_repeat_offenders LIMIT 10""")
    districts = query(paths, "SELECT * FROM mart_district LIMIT 10")
    reasons = query(paths, """SELECT reason_primary, sum(flags)::INT AS flags FROM mart_reasons
                              GROUP BY 1 ORDER BY 2 DESC LIMIT 8""")
    qr = query(paths, """SELECT qr_brand, sum(is_spurious::INT)::INT AS spurious_batches,
                                sum((NOT is_spurious)::INT)::INT AS nsq_batches
                         FROM mart_qr_brands WHERE report_month >= DATE '2023-08-01'
                         GROUP BY 1 ORDER BY 2 DESC""")
    quality = pd.read_csv(paths.exports / "quality_report.csv") if (paths.exports / "quality_report.csv").exists() else pd.DataFrame()
    mpath = paths.exports / "model_metrics.json"
    metrics = json.loads(mpath.read_text()) if mpath.exists() else {}

    banner = ("> **SAMPLE DATA.** Every company and brand below is invented. These numbers test the "
              "pipeline; they say nothing about real manufacturers.\n\n" if label == "SAMPLE" else "")
    text = f"""# Findings ({label} data)

{banner}Coverage: {cov['first_month']:%Y-%m} to {cov['last_month']:%Y-%m} ({cov['months']} months), {cov['flags']} flagged batches ({cov['spurious']} spurious).
{cov['spellings']} manufacturer spellings resolved to {cov['companies']} companies and {cov['sites']} factory sites.

Wording rule: a row here means "flagged in a CDSCO alert" for one tested batch. It is not a judgement on a company.

## Repeat offenders (sites flagged in 3+ months)
{_md(offenders)}
## Where flagged factories are
{_md(districts)}
## Why medicines failed
{_md(reasons)}
## QR-coded brands in alerts since Aug 2023
{_md(qr)}
## Re-flag model
"""
    if metrics.get("skipped"):
        text += metrics["reason"] + "\n\n"
    elif metrics:
        text += (f"Test period {metrics['test_period']}. Watch list of {metrics['top_k']} sites per month.\n\n"
                 f"| | model | baseline (last 6 months) |\n|---|---|---|\n"
                 f"| precision@{metrics['top_k']} | {metrics['model_precision_at_k']} | {metrics['baseline_precision_at_k']} |\n"
                 f"| average precision | {metrics['model_average_precision']} | {metrics['baseline_average_precision']} |\n\n"
                 f"Model beat the baseline in {metrics['months_model_beats_baseline']} of {metrics['test_months']} "
                 f"test months (tied in {metrics['months_tied']}). Base rate: {metrics['base_rate']}.\n\n"
                 f"{metrics['caveat']}\n\n")
    text += "## Data quality\n" + _md(quality, 30)
    path = paths.exports / "findings.md"
    path.write_text(text, encoding="utf-8")
    return path
