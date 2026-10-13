"""Stage 8: export the marts as CSV and Parquet for Power BI (or Excel).

In Power BI Desktop: Get data -> Text/CSV (or Parquet) -> pick files in
data/exports/powerbi/. Relate fct_flags.site_id to dim_site.site_id.
"""
from __future__ import annotations

import logging

from . import config
from .warehouse import query

log = logging.getLogger(__name__)

TABLES = ["fct_flags", "dim_site", "mart_monthly", "mart_repeat_offenders", "mart_district",
          "mart_reasons", "mart_state_reporting", "mart_pending_states", "mart_qr_brands",
          "mart_parse_quality"]


def export_all(paths: config.Paths) -> None:
    out = paths.exports / "powerbi"
    out.mkdir(parents=True, exist_ok=True)
    label = config.dataset_label(paths)
    for t in TABLES:
        df = query(paths, f"SELECT * FROM {t}")
        df.insert(0, "dataset", label)
        df.to_csv(out / f"{t}.csv", index=False)
        df.to_parquet(out / f"{t}.parquet", index=False)
    log.info("Exported %d tables to %s", len(TABLES), out)
    print(f"Exported {len(TABLES)} tables to {out}")
