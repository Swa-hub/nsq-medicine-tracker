"""Small text helpers shared by every stage: whitespace, months and dates."""
from __future__ import annotations

import re
import unicodedata

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2, "febuary": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_WORD = r"(jan(?:uary)?|feb(?:ruary|uary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def clean_ws(text: object) -> str:
    """Normalise unicode, turn every run of whitespace into one space, strip."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    # Mojibake that shows up in CDSCO titles ("Januaryâ 2025")
    s = s.replace("â\u0080\u0093", "-").replace("â", " ")
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def find_month_year(text: str) -> tuple[int | None, int | None]:
    """Find the report month and year in a title like
    'NSQ ALERT FOR THE MONTH OF MAY-2025' or 'Drug Alert list for month of Feb 2021'.

    Returns (year, month); either can be None.
    """
    t = clean_ws(text).lower()
    year = None
    years = re.findall(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", t)
    if years:
        year = int(years[-1])
    m = re.search(rf"\b{_MONTH_WORD}\b", t)
    month = MONTHS.get(m.group(1)) if m else None
    return year, month


def parse_month_year(raw: object) -> str | None:
    """Turn the many date styles in CDSCO lists into 'YYYY-MM'.

    Handles: 05/2024, 5/24, 05-2024, 2024-05, May-24, May 2024, May.2024,
    Apr-24, 12/2024, 01.2025, 'MAY/2024'. Returns None when unsure.
    """
    s = clean_ws(raw).lower().replace(" ", "")
    if not s or s in {"-", "na", "n/a", "nil", "notmentioned"}:
        return None

    def _yy(y: str) -> int:
        y_int = int(y)
        return 2000 + y_int if y_int < 100 else y_int

    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{2}|\d{4})", s)
    if m:
        mon, yr = int(m.group(1)), _yy(m.group(2))
        if 1 <= mon <= 12:
            return f"{yr:04d}-{mon:02d}"
    m = re.fullmatch(r"(\d{4})[/\-.](\d{1,2})", s)
    if m:
        yr, mon = int(m.group(1)), int(m.group(2))
        if 1 <= mon <= 12:
            return f"{yr:04d}-{mon:02d}"
    m = re.fullmatch(rf"{_MONTH_WORD}[/\-.,']?(\d{{2}}|\d{{4}})", s)
    if m:
        return f"{_yy(m.group(2)):04d}-{MONTHS[m.group(1)]:02d}"
    # A full date like 12/05/2024 or 12-05-2024 -> keep month and year
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})", s)
    if m:
        mon, yr = int(m.group(2)), _yy(m.group(3))
        if 1 <= mon <= 12:
            return f"{yr:04d}-{mon:02d}"
    return None


def norm_key(text: object) -> str:
    """Lowercase, letters and digits only, single spaces. Used for matching."""
    s = clean_ws(text).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()
