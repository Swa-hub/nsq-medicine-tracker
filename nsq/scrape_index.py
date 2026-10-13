"""Stage 1a: read CDSCO's Alerts page and build an index of every alert list.

The Alerts page is one long table: S.no | Title | Release Date | PDF link | Size.
It mixes monthly drug-quality lists with unrelated notices (device alerts,
NOC lists, circulars), so each title is classified here.
"""
from __future__ import annotations

import base64
import datetime as dt
import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import config
from .textutil import clean_ws, find_month_year

log = logging.getLogger(__name__)

_EXCLUDE = (
    "medical device", "ivd", "in-vitro", "invitro", "diagnostic", "noc ",
    "noc of", "ba-be", "ba/be", "bio-analytical", "bioanalytical", "circular",
    "gst", "smpc", "factsheet", "recall", "theft", "corrigendum", "guideline",
    "minutes of meeting", "rc holders", "checklist", "import drugs",
    "advisory", "revoke", "non-conviction", "destroy", "manufacturing units",
    "new link", "clarifications", "pholcodine", "registred", "registered ba",
    "falsified", "who alert", "biosimilar",
)


def classify_title(title: str) -> dict:
    """Decide what kind of list a title describes.

    Returns doc_type (central_nsq, state_nsq, spurious, combined, compiled,
    product_alert, other), report_year, report_month and is_revised.
    """
    t = clean_ws(title).lower()
    year, month = find_month_year(t)
    is_revised = bool(re.search(r"\brevis(?:e|ed)\b", t))
    out = {"doc_type": "other", "report_year": year, "report_month": month,
           "is_revised": is_revised}

    if "samples declared nsq" in t or re.search(r"nsq\s+20\d\d\s*-\s*20\d\d", t):
        out.update(doc_type="compiled", report_month=None)
        return out
    is_declared_list = "declared as" in t  # "List of Drugs, Medical Devices ... declared as ..."
    if not is_declared_list and any(k in t for k in _EXCLUDE):
        return out

    is_state = "state" in t
    is_spurious_only = "spurious" in t and "standard quality" not in t and "nsq" not in t
    mentions_quality = any(k in t for k in ("nsq", "standard quality", "drug alert", "drugs alert"))

    if month is None or year is None:
        # NSQ notices about a single product (e.g. one vaccine batch)
        if "nsq" in t or "standard quality" in t:
            out["doc_type"] = "product_alert"
        return out

    if is_spurious_only:
        out["doc_type"] = "spurious"
    elif is_state and (mentions_quality or is_declared_list):
        out["doc_type"] = "state_nsq"
    elif "cdsco lab" in t or "nsq" in t or ("standard quality" in t and "spurious" not in t):
        out["doc_type"] = "central_nsq"
    elif mentions_quality or is_declared_list:
        out["doc_type"] = "combined"
    return out


def decode_num_id(url: str) -> int | None:
    """CDSCO links end in num_id=<base64 of a number>, e.g. MTI5Mjc= -> 12927."""
    try:
        q = parse_qs(urlparse(url).query)
        raw = q.get("num_id", [None])[0]
        if not raw:
            return None
        return int(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii"))
    except Exception:  # noqa: BLE001 - any odd id just means "unknown"
        return None


def parse_release_date(text: str) -> str | None:
    s = clean_ws(text)
    for fmt in ("%Y-%b-%d", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_alerts_table(html: str, base_url: str = config.BASE_URL) -> pd.DataFrame:
    """Turn the Alerts page HTML into one row per listed file."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        link = None
        for td in tds:
            a = td.find("a", href=True)
            if a:
                link = urljoin(base_url, a["href"])
                break
        if not link:
            continue
        sno = clean_ws(tds[0].get_text())
        title = clean_ws(tds[1].get_text())
        release = parse_release_date(tds[2].get_text())
        size = clean_ws(tds[4].get_text()) if len(tds) > 4 else ""
        rows.append({"sno": sno, "title": title, "release_date": release,
                     "url": link, "size_text": size, "num_id": decode_num_id(link)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    meta = df["title"].apply(classify_title).apply(pd.Series)
    df = pd.concat([df, meta], axis=1)
    df = _refine_types(df)
    df["date_suspect"] = df.apply(_date_suspect, axis=1)
    return df


def _refine_types(df: pd.DataFrame) -> pd.DataFrame:
    """A plain 'Drug Alert for the Month of X' published next to a
    'State Drug Alert' for the same month is really the central-lab list."""
    df = df.copy()
    has_state = set(
        df.loc[df["doc_type"] == "state_nsq", ["report_year", "report_month"]]
        .dropna().astype(int).itertuples(index=False, name=None)
    )
    mask = (df["doc_type"] == "combined") & ~df["is_revised"]
    for i in df.index[mask]:
        key = (df.at[i, "report_year"], df.at[i, "report_month"])
        if pd.notna(key[0]) and (int(key[0]), int(key[1])) in has_state:
            df.at[i, "doc_type"] = "central_nsq"
    return df


def _date_suspect(row: pd.Series) -> bool:
    """True when the title's month is after the release date (a CDSCO typo)."""
    if pd.isna(row.get("report_year")) or pd.isna(row.get("report_month")) or not row.get("release_date"):
        return False
    rel = dt.date.fromisoformat(row["release_date"])
    return (int(row["report_year"]), int(row["report_month"])) > (rel.year, rel.month)


def fetch_alerts_html(session: requests.Session | None = None, url: str = config.ALERTS_URL) -> str:
    session = session or make_session()
    resp = session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.text


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT})
    return s


def build_index(paths: config.Paths, html: str | None = None) -> pd.DataFrame:
    """Fetch (or reuse) the Alerts page, save a snapshot, write alert_index.csv."""
    paths.ensure()
    if html is None:
        html = fetch_alerts_html()
        snap = paths.raw / f"alerts_page_{dt.date.today():%Y%m%d}.html"
        snap.write_text(html, encoding="utf-8")
        log.info("Saved page snapshot to %s", snap)
    df = parse_alerts_table(html)
    df.to_csv(paths.alert_index, index=False)
    kept = df[df["doc_type"].isin(config.DRUG_LIST_TYPES)]
    log.info("Index: %d files listed, %d are drug-quality lists", len(df), len(kept))
    return df
