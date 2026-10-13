"""Stage 1b: download the drug-quality PDFs listed in the index, politely.

* waits a few seconds between requests
* skips files already on disk, so a re-run only fetches new months
* checks every file really is a PDF
* records a manifest row per file: where it came from, size and SHA-256 hash
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import time
from pathlib import Path

import re
from urllib.parse import quote, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import config
from .scrape_index import make_session

log = logging.getLogger(__name__)


def local_name(row: pd.Series) -> str:
    """Deterministic file name, e.g. 2025-06_state_nsq_12926.pdf"""
    rev = "_rev" if bool(row.get("is_revised")) else ""
    nid = int(row["num_id"]) if pd.notna(row.get("num_id")) else abs(hash(row["url"])) % 10**8
    if pd.notna(row.get("report_year")) and pd.notna(row.get("report_month")):
        stem = f"{int(row['report_year'])}-{int(row['report_month']):02d}"
    elif pd.notna(row.get("report_year")):
        stem = f"{int(row['report_year'])}-00"
    else:
        stem = "undated"
    return f"{stem}_{row['doc_type']}{rev}_{nid}.pdf"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def select_files(index: pd.DataFrame, start_year: int, types=config.DRUG_LIST_TYPES) -> pd.DataFrame:
    sel = index[index["doc_type"].isin(types)].copy()
    year_ok = sel["report_year"].fillna(0).astype(int) >= start_year
    sel = sel[year_ok | (sel["doc_type"] == "compiled")]
    return sel.sort_values(["report_year", "report_month"], na_position="first")


def _get_with_retry(session: requests.Session, url: str, tries: int = 3) -> requests.Response:
    last_err: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return resp
            last_err = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as e:
            last_err = e
        wait = config.REQUEST_DELAY_SECONDS * (2 ** attempt)
        log.warning("Attempt %d for %s failed (%s); waiting %.0fs", attempt, url, last_err, wait)
        time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}: {last_err}")


def _pdf_start(content: bytes) -> int:
    """Where the PDF starts. Some servers send a few blank lines first."""
    return content.find(b"%PDF", 0, 2048)


def find_pdf_link(html: str, page_url: str) -> str | None:
    """CDSCO's download_file_division.jsp answers with a tiny HTML page that
    links to the real file, e.g.
    /opencms/resources/UploadCDSCOWeb/2018/UploadAlertsFiles/CDSCO NSQ june25.pdf
    Find that link (or an iframe, embed, meta refresh or JS redirect)."""
    soup = BeautifulSoup(html, "lxml")
    cands = [a["href"] for a in soup.find_all("a", href=True)]
    cands += [t["src"] for t in soup.find_all(["iframe", "embed"], src=True)]
    cands += [o["data"] for o in soup.find_all("object", data=True)]
    for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
        m = re.search(r"url\s*=\s*['\"]?([^'\";]+)", meta.get("content", ""), re.I)
        if m:
            cands.append(m.group(1))
    cands += re.findall(r"""(?:location(?:\.href)?\s*=|window\.open\(|location\.replace\()\s*['"]([^'"]+)""", html)
    cands = [c.strip() for c in cands if c and not c.strip().startswith(("#", "javascript:", "mailto:"))]
    pdfs = [c for c in cands if ".pdf" in c.lower()]
    pick = (pdfs or cands or [None])[0]
    if not pick:
        return None
    # Keep the path readable but safe: spaces etc. become %20, existing %xx stay.
    return urljoin(page_url, quote(pick, safe="/:%?=&~+,;@!$'()*#"))


def fetch_pdf(session: requests.Session, url: str, debug_dir: Path | None = None,
              debug_name: str = "page") -> tuple[bytes | None, str | None]:
    """Return (pdf bytes, final url), following CDSCO's link page if needed."""
    resp = _get_with_retry(session, url)
    start = _pdf_start(resp.content)
    if start >= 0:
        return resp.content[start:], url
    link = find_pdf_link(resp.text, resp.url or url)
    if link and link != url:
        time.sleep(config.REQUEST_DELAY_SECONDS)
        resp2 = _get_with_retry(session, link)
        start = _pdf_start(resp2.content)
        if start >= 0:
            return resp2.content[start:], link
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{Path(debug_name).stem}.html").write_bytes(resp.content[:200_000])
    return None, None


def download_all(paths: config.Paths, start_year: int = config.DEFAULT_START_YEAR,
                 limit: int | None = None, session: requests.Session | None = None) -> pd.DataFrame:
    paths.ensure()
    index = pd.read_csv(paths.alert_index)
    todo = select_files(index, start_year)
    if limit:
        todo = todo.tail(limit)
    session = session or make_session()
    session.headers.setdefault("Referer", config.ALERTS_URL)

    old = pd.read_csv(paths.manifest) if paths.manifest.exists() else pd.DataFrame()
    records = []
    for _, row in todo.iterrows():
        name = local_name(row)
        dest = paths.pdfs / name
        rec = row.to_dict() | {"local_file": name, "status": "", "bytes": None,
                               "sha256": None, "downloaded_at": None}
        if dest.exists() and dest.stat().st_size > 0:
            rec.update(status="cached", bytes=dest.stat().st_size, sha256=sha256_of(dest))
            records.append(rec)
            continue
        try:
            pdf_bytes, pdf_url = fetch_pdf(session, row["url"], debug_dir=paths.raw / "not_pdf",
                                           debug_name=name)
            if pdf_bytes is None:
                rec["status"] = "not_pdf"
                log.warning("Not a PDF: %s (%s). The page was saved to raw/not_pdf/ for a look.",
                            row["title"], row["url"])
            else:
                dest.write_bytes(pdf_bytes)
                rec.update(status="downloaded", bytes=len(pdf_bytes), sha256=sha256_of(dest),
                           pdf_url=pdf_url,
                           downloaded_at=dt.datetime.now().isoformat(timespec="seconds"))
                print(f"  saved {name}")
        except RuntimeError as e:
            rec["status"] = f"error: {e}"
            log.error("%s", e)
        records.append(rec)
        time.sleep(config.REQUEST_DELAY_SECONDS)

    new = pd.DataFrame(records)
    manifest = pd.concat([old, new], ignore_index=True) if not old.empty else new
    if not manifest.empty:
        manifest = manifest.drop_duplicates(subset=["local_file"], keep="last")
    manifest.to_csv(paths.manifest, index=False)
    log.info("Manifest: %d files (%s)", len(manifest),
             manifest["status"].value_counts().to_dict() if not manifest.empty else {})
    return manifest
