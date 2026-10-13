"""Paths and settings in one place.

Everything a stage reads or writes lives under one data directory, so the
real run (``data/``) and the demo run (``data_demo/``) never mix.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ALERTS_URL = "https://cdsco.gov.in/opencms/opencms/en/Notifications/Alerts/"
BASE_URL = "https://cdsco.gov.in"
PORTAL_URL = "https://cdscoonline.gov.in/CDSCO/viewPublicNSQDrug"

# Be polite to a government server: one request every few seconds.
REQUEST_DELAY_SECONDS = float(os.environ.get("NSQ_REQUEST_DELAY", "3"))
REQUEST_TIMEOUT_SECONDS = 60
USER_AGENT = os.environ.get(
    "NSQ_USER_AGENT",
    "nsq-tracker/0.1 (student research project; polite crawler)",
)

# Alert lists we keep. Everything else on the Alerts page (device alerts,
# NOC lists, BA/BE centres...) is ignored.
DRUG_LIST_TYPES = ("central_nsq", "state_nsq", "spurious", "combined", "compiled")

DEFAULT_START_YEAR = 2019


@dataclass(frozen=True)
class Paths:
    data: Path

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def pdfs(self) -> Path:
        return self.raw / "pdfs"

    @property
    def portal(self) -> Path:
        return self.raw / "portal"

    @property
    def interim(self) -> Path:
        return self.data / "interim"

    @property
    def reference(self) -> Path:
        return self.data / "reference"

    @property
    def warehouse(self) -> Path:
        return self.data / "warehouse" / "nsq.duckdb"

    @property
    def exports(self) -> Path:
        return self.data / "exports"

    @property
    def alert_index(self) -> Path:
        return self.raw / "alert_index.csv"

    @property
    def manifest(self) -> Path:
        return self.raw / "manifest.csv"

    @property
    def rows(self) -> Path:
        return self.interim / "nsq_rows.parquet"

    @property
    def doc_summary(self) -> Path:
        return self.interim / "doc_summary.csv"

    @property
    def clean_rows(self) -> Path:
        return self.interim / "nsq_clean.parquet"

    @property
    def manufacturer_map(self) -> Path:
        return self.interim / "manufacturer_map.parquet"

    @property
    def dataset_label(self) -> Path:
        """A one-line file saying whether this folder holds REAL or SAMPLE data."""
        return self.data / "DATASET.txt"

    def ensure(self) -> "Paths":
        for p in (self.pdfs, self.portal, self.interim, self.reference,
                  self.warehouse.parent, self.exports):
            p.mkdir(parents=True, exist_ok=True)
        return self


def get_paths(data_dir: str | os.PathLike | None = None) -> Paths:
    if data_dir is None:
        data_dir = os.environ.get("NSQ_DATA_DIR", ROOT / "data")
    return Paths(Path(data_dir).resolve())


def dataset_label(paths: Paths) -> str:
    """Return 'REAL' or 'SAMPLE' so every output can say which one it is."""
    if paths.dataset_label.exists():
        return paths.dataset_label.read_text(encoding="utf-8").strip().split()[0].upper()
    return "REAL"
