"""Optional: read table rows from pages the table parser could not handle.

Used only with `python -m nsq parse --llm` and only for pages where
pdfplumber found text that looks like numbered rows but no table (for example
a scanned-then-OCR'd page or a list without cell borders).

Guard rails, because an LLM can invent things:
* it only ever sees one page of text and must copy values, not infer them
* every row it returns is checked: its batch number and drug name must
  appear in the page text, or the row is thrown away
* every kept or rejected row is logged to interim/llm_log.jsonl
* kept rows are marked parse_method = "llm" so they can be filtered out

Needs `pip install anthropic` and an ANTHROPIC_API_KEY environment variable.
"""
from __future__ import annotations

import json
import logging
import os
import re

from .textutil import norm_key

log = logging.getLogger(__name__)

MODEL = os.environ.get("NSQ_LLM_MODEL", "claude-haiku-5-5")

PROMPT = """You are extracting rows from one page of an Indian drug regulator (CDSCO) alert list.
Copy values exactly as written on the page. Do not guess, infer or fix anything.
If a value is not on the page, use an empty string.

Return ONLY a JSON array. One object per numbered row, with these keys:
sno, drug_name, batch_no, mfg_raw, exp_raw, manufacturer_raw, reason_raw, reported_by

Page text:
<<<
{page}
>>>"""


def _client():
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("pip install anthropic to use --llm") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("Set ANTHROPIC_API_KEY to use --llm")
    return anthropic.Anthropic()


def _parse_json(text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


def grounded(row: dict, page_text: str) -> bool:
    """True only if the key values really appear on the page."""
    page = norm_key(page_text)
    for k in ("batch_no", "drug_name"):
        v = norm_key(row.get(k, ""))
        if not v or v not in page:
            return False
    return True


def extract_rows_from_pages(res, meta: dict) -> list[dict]:
    from .parse_pdf import _finish_row  # noqa: PLC0415

    client = _client()
    out = []
    log_path = None
    for pno in res.pages_without_tables:
        page_text = res.page_texts.get(pno, "")
        msg = client.messages.create(model=MODEL, max_tokens=4000, temperature=0,
                                     messages=[{"role": "user", "content": PROMPT.format(page=page_text)}])
        text = "".join(getattr(b, "text", "") for b in msg.content)
        for i, row in enumerate(_parse_json(text)):
            row = {k: str(row.get(k, "") or "") for k in
                   ("sno", "drug_name", "batch_no", "mfg_raw", "exp_raw", "manufacturer_raw",
                    "reason_raw", "reported_by")}
            ok = grounded(row, page_text)
            entry = {"file": meta.get("local_file"), "page": pno, "row": row, "kept": ok}
            if log_path is None:
                from pathlib import Path  # noqa: PLC0415
                log_path = Path(meta.get("_interim", ".")) / "llm_log.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            if not ok:
                continue
            row.update(drawn_by="", page=pno, row_idx=1000 + i, section="unknown", parse_method="llm")
            _finish_row(row, meta)
            out.append(row)
    log.info("LLM fallback kept %d rows from %d pages", len(out), len(res.pages_without_tables))
    return out
