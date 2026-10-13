"""Stage 4: entity resolution - decide which spellings are the same manufacturer.

CDSCO lists write one factory many ways:
  'M/s. Arvex Lifesciences Pvt. Ltd., Baddi'  /  'Arvex Life Sciences Private Limited'
  'M/S Arvex Lifesceinces (P) Ltd.'           /  'Arvx Lifesciences Pvt Ltd' (typo)

Two levels are built:
* company - same business name (fuzzy match on the cleaned key)
* site    - one factory of that company (company + PIN code, else district)
Repeat-offender analysis works at site level, because quality failures
belong to a factory, not to a head office.

How it works: block by the first letters of the name (so we never compare
every pair), score pairs with rapidfuzz, link pairs above a threshold, and
take connected groups (union-find) as companies.
"""
from __future__ import annotations

import logging
import random
from collections import Counter, defaultdict

import pandas as pd
from rapidfuzz import fuzz

from . import config

log = logging.getLogger(__name__)

# token_sort_ratio (0-100) needed to call two cleaned names the same company:
NAME_THRESHOLD = 96           # names alone, at different addresses
NAME_THRESHOLD_SAME_PIN = 85  # looser when both spellings share a PIN code (typos at one factory)
NAME_THRESHOLD_NO_PIN = 93    # one side has no PIN, so the address can't confirm or refute


class UnionFind:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _block_key(key: str) -> str:
    first = key.split()[0] if key else ""
    return first[:3]


def resolve_companies(keys: pd.DataFrame) -> dict[str, str]:
    """keys: columns mfr_key, pins (set). Returns mfr_key -> company root key."""
    uf = UnionFind(keys["mfr_key"])
    blocks = defaultdict(list)
    for k, pins in zip(keys["mfr_key"], keys["pins"]):
        blocks[_block_key(k)].append((k, pins))
    # Typos in the first letters would escape their block, so also block on the
    # second word (e.g. 'lifesciences', 'remedies' + first letter).
    for k, pins in zip(keys["mfr_key"], keys["pins"]):
        parts = k.split()
        if len(parts) > 1:
            blocks[f"{k[:1]}|{parts[1][:4]}"].append((k, pins))
    for members in blocks.values():
        for i in range(len(members)):
            ki, pi = members[i]
            for j in range(i + 1, len(members)):
                kj, pj = members[j]
                score = fuzz.token_sort_ratio(ki, kj)
                if (score >= NAME_THRESHOLD
                        or (score >= NAME_THRESHOLD_SAME_PIN and pi & pj)
                        or (score >= NAME_THRESHOLD_NO_PIN and (not pi or not pj))):
                    uf.union(ki, kj)
    return {k: uf.find(k) for k in keys["mfr_key"]}


def build_manufacturer_map(clean: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct (mfr_key, pin, district): its company_id and site_id."""
    df = clean[clean["mfr_key"].fillna("") != ""].copy()
    df["mfr_pin"] = df["mfr_pin"].fillna("")
    df["mfr_district"] = df["mfr_district"].fillna("")
    pins = df.groupby("mfr_key")["mfr_pin"].agg(lambda s: {p for p in s if p}).rename("pins")
    keys = pins.reset_index()
    root = resolve_companies(keys)
    df["company_root"] = df["mfr_key"].map(root)

    # Readable company ids and names
    names = (df.groupby("company_root")["mfr_name"]
             .agg(lambda s: Counter(s).most_common(1)[0][0]))
    roots_sorted = sorted(names.index)
    company_id = {r: f"CO{i:05d}" for i, r in enumerate(roots_sorted)}

    # Sites: company + PIN. Rows with no PIN join the company's only site in
    # the same district, else become a "<district>" or "unknown" site.
    site_rows = []
    for root_key, g in df.groupby("company_root"):
        cid = company_id[root_key]
        pin_district = {p: Counter(gg["mfr_district"]).most_common(1)[0][0]
                        for p, gg in g[g["mfr_pin"] != ""].groupby("mfr_pin")}
        district_pins = defaultdict(set)
        for p, d in pin_district.items():
            district_pins[d].add(p)
        for (key, pin, dist), _ in g.groupby(["mfr_key", "mfr_pin", "mfr_district"]):
            if pin:
                site = f"{cid}-{pin}"
            elif dist and len(district_pins.get(dist, ())) == 1:
                site = f"{cid}-{next(iter(district_pins[dist]))}"
            elif len(pin_district) == 1:
                site = f"{cid}-{next(iter(pin_district))}"
            else:
                site = f"{cid}-{(dist or 'unknown').lower().replace(' ', '_')}"
            site_rows.append({"mfr_key": key, "mfr_pin": pin, "mfr_district": dist,
                              "company_id": cid, "company_name": names[root_key], "site_id": site})
    m = pd.DataFrame(site_rows)
    log.info("Entities: %d spellings -> %d companies, %d sites",
             df["mfr_key"].nunique(), m["company_id"].nunique(), m["site_id"].nunique())
    return m


def link(paths: config.Paths) -> pd.DataFrame:
    clean = pd.read_parquet(paths.clean_rows)
    m = build_manufacturer_map(clean)
    m.to_parquet(paths.manufacturer_map, index=False)
    write_review_sample(clean, m, paths)
    return m


def write_review_sample(clean: pd.DataFrame, m: pd.DataFrame, paths: config.Paths,
                        n: int = 100, seed: int = 1) -> None:
    """Pairs for a human to check: half predicted 'same', half 'different but similar'.
    Fill the 'correct' column with yes/no, then run `python -m nsq score-review`."""
    rng = random.Random(seed)
    j = clean.merge(m, on=["mfr_key"], how="inner", suffixes=("", "_m"))
    raw = j.drop_duplicates("manufacturer_raw")[["manufacturer_raw", "company_id"]]
    same, diff = [], []
    by_company = raw.groupby("company_id")["manufacturer_raw"].apply(list)
    for cid, lst in by_company.items():
        if len(lst) > 1:
            a, b = rng.sample(lst, 2)
            same.append((a, b, "same company"))
    rows = raw.to_records(index=False)
    for _ in range(4000):
        if len(diff) >= n // 2 or len(rows) < 2:
            break
        (a, ca), (b, cb) = rng.sample(list(rows), 2)
        if ca != cb and fuzz.token_sort_ratio(a, b) >= 60:
            diff.append((a, b, "different companies"))
    pairs = rng.sample(same, min(len(same), n // 2)) + diff[: n // 2]
    out = pd.DataFrame(pairs, columns=["spelling_a", "spelling_b", "pipeline_says"])
    out["correct"] = ""
    paths.exports.mkdir(parents=True, exist_ok=True)
    out.to_csv(paths.exports / "match_review_sample.csv", index=False)


def bcubed(pred: pd.Series, truth: pd.Series) -> dict:
    """B-cubed precision/recall: for each row, how much of its predicted group
    is truly the same company, and how much of its true group we found."""
    df = pd.DataFrame({"p": pred.values, "t": truth.values})
    p_groups = df.groupby("p").groups
    t_groups = df.groupby("t").groups
    prec = rec = 0.0
    for idx, row in df.iterrows():
        P = set(p_groups[row["p"]])
        T = set(t_groups[row["t"]])
        inter = len(P & T)
        prec += inter / len(P)
        rec += inter / len(T)
    n = len(df)
    p, r = prec / n, rec / n
    return {"bcubed_precision": round(p, 4), "bcubed_recall": round(r, 4),
            "bcubed_f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "rows": n}
