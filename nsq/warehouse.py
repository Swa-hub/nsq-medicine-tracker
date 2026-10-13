"""Stage 5: load everything into a DuckDB file and build the tables in layers.

DuckDB is a single-file database (like SQLite, but built for analytics), so
there is no server to install. The SQL lives in nsq/sql/ in three layers,
the same way dbt projects are organised:

  01_staging.sql      typed copies of the inputs
  02_intermediate.sql one row per flagged batch, de-duplicated and linked
  03_marts.sql        tables the dashboard, Power BI and the model read
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from . import config
from .textutil import norm_key

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).parent / "sql"


def load_qr_brands(paths: config.Paths) -> list[str]:
    for p in (paths.reference / "qr_brands.csv", config.ROOT / "data" / "reference" / "qr_brands.csv"):
        if p.exists():
            df = pd.read_csv(p)
            return [b for b in df["brand"].dropna().astype(str) if b.strip()]
    return []


def tag_qr_brand(drug_name: str, brand: str, qr_brands: list[str]) -> str | None:
    """Return the QR brand this row names, matching on the brand's first word
    (e.g. 'Feverol 650' matches 'Paracetamol Tablets IP 650 mg (Feverol-650)')."""
    text = norm_key(f"{brand} {drug_name}")
    for b in qr_brands:
        first = norm_key(b).split()[0] if norm_key(b) else ""
        if first and len(first) >= 4 and re.search(rf"\b{re.escape(first)}\b", text):
            return b
    return None


def build(paths: config.Paths) -> Path:
    clean = pd.read_parquet(paths.clean_rows)
    qr = load_qr_brands(paths)
    clean["qr_brand"] = [tag_qr_brand(d or "", b or "", qr) for d, b in
                         zip(clean["drug_name"], clean["drug_brand"])]
    for c in ("mfr_pin", "mfr_district"):
        clean[c] = clean[c].fillna("")
    mmap = pd.read_parquet(paths.manufacturer_map)
    index = pd.read_csv(paths.alert_index) if paths.alert_index.exists() else pd.DataFrame(
        columns=["num_id", "url"])
    summary = pd.read_csv(paths.doc_summary) if paths.doc_summary.exists() else pd.DataFrame()
    pend_path = paths.interim / "portal_pending_states.csv"
    pending = pd.read_csv(pend_path) if pend_path.exists() else pd.DataFrame(
        columns=["report_year", "report_month", "state"])

    paths.warehouse.parent.mkdir(parents=True, exist_ok=True)
    if paths.warehouse.exists():
        paths.warehouse.unlink()
    con = duckdb.connect(str(paths.warehouse))
    try:
        for name, df in {"clean_rows": clean, "manufacturer_map": mmap, "alert_index": index,
                         "doc_summary": summary, "pending_states": pending}.items():
            con.register(f"{name}_df", df)
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {name}_df")
            con.unregister(f"{name}_df")
        for sql_file in sorted(SQL_DIR.glob("*.sql")):
            con.execute(sql_file.read_text(encoding="utf-8"))
            log.info("Ran %s", sql_file.name)
        n = con.execute("SELECT count(*) FROM fct_flags").fetchone()[0]
        log.info("Warehouse ready: %s (%d flags)", paths.warehouse, n)
    finally:
        con.close()
    return paths.warehouse


def query(paths: config.Paths, sql: str, params: list | None = None) -> pd.DataFrame:
    con = duckdb.connect(str(paths.warehouse), read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()
