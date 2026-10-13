"""Command line: python -m nsq <command> [--data-dir DIR]

Real data (run on your own computer, it needs internet):
  python -m nsq collect             index the Alerts page + download PDFs (+ portal files you saved)
  python -m nsq build               parse -> clean -> link -> warehouse -> checks -> model -> exports
  python -m nsq all                 collect + build

Demo with invented sample data (no internet needed):
  python -m nsq demo                writes everything to data_demo/

Single stages: index, download, portal, parse, clean, link, warehouse, quality,
model, export, report, score-review
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import config


def _stage(name: str):
    print(f"\n== {name} ==", flush=True)
    return time.time()


def cmd_index(p, a):
    import requests

    from .scrape_index import build_index
    try:
        df = build_index(p)
    except requests.RequestException as e:
        sys.exit(f"Could not reach the CDSCO Alerts page ({e}).\n"
                 "Check your internet connection. If the site answers 403, set a browser-like "
                 "user agent: $env:NSQ_USER_AGENT = 'Mozilla/5.0' (PowerShell) or "
                 "export NSQ_USER_AGENT='Mozilla/5.0' (Mac).")
    kept = df[df["doc_type"].isin(config.DRUG_LIST_TYPES)] if len(df) else df
    print(f"Indexed {len(df)} files on the Alerts page; {len(kept)} are drug-quality lists.")


def cmd_download(p, a):
    from .download import download_all
    download_all(p, start_year=a.start_year, limit=a.limit)


def cmd_portal(p, a):
    from .portal import load_portal
    load_portal(p)


def cmd_parse(p, a):
    from .parse_pdf import parse_all
    from .portal import load_portal
    load_portal(p)
    rows, summary = parse_all(p, use_llm=a.llm)
    checks = summary["count_check"].value_counts().to_dict() if "count_check" in summary else {}
    print(f"Parsed {len(rows)} rows from {len(summary)} files. Count checks: {checks}")


def cmd_clean(p, a):
    from .clean import clean_rows
    clean_rows(p)


def cmd_link(p, a):
    from .entities import link
    m = link(p)
    print(f"{m['company_id'].nunique()} companies, {m['site_id'].nunique()} sites")


def cmd_warehouse(p, a):
    from .warehouse import build
    build(p)


def cmd_quality(p, a):
    from .quality import run_checks
    res = run_checks(p)
    print(res.to_string(index=False))
    if a.strict and (res["status"] == "FAIL").any():
        sys.exit(1)


def cmd_model(p, a):
    from .model import train_and_evaluate
    metrics = train_and_evaluate(p, horizon=a.horizon, top_k=a.top_k)
    if metrics.get("skipped"):
        print(metrics["reason"])
        return
    print(json.dumps({k: v for k, v in metrics.items() if k != "monthly"}, indent=2))


def cmd_export(p, a):
    from .export import export_all
    export_all(p)


def cmd_report(p, a):
    from .report import write_report
    path = write_report(p)
    print(f"Wrote {path}")


def cmd_score_review(p, a):
    import pandas as pd
    f = p.exports / "match_review_sample.csv"
    df = pd.read_csv(f)
    done = df["correct"].astype(str).str.lower().isin(["yes", "no", "y", "n"])
    if not done.any():
        print(f"Fill the 'correct' column in {f} with yes/no first.")
        return
    ok = df.loc[done, "correct"].astype(str).str.lower().str.startswith("y").mean()
    print(f"Matching accuracy on {int(done.sum())} hand-checked pairs: {ok:.1%}")


def cmd_build(p, a):
    for name, fn in (("parse", cmd_parse), ("clean", cmd_clean), ("link", cmd_link),
                     ("warehouse", cmd_warehouse), ("quality", cmd_quality), ("model", cmd_model),
                     ("export", cmd_export), ("report", cmd_report)):
        t = _stage(name)
        fn(p, a)
        print(f"   ({time.time() - t:.1f}s)")


def cmd_collect(p, a):
    for name, fn in (("index", cmd_index), ("download", cmd_download)):
        t = _stage(name)
        fn(p, a)
        print(f"   ({time.time() - t:.1f}s)")


def cmd_all(p, a):
    cmd_collect(p, a)
    cmd_build(p, a)


def cmd_demo(p, a):
    from .sample import make_sample
    if a.data_dir is None:
        p = config.get_paths(config.ROOT / "data_demo")
    t = _stage("make sample data (invented companies)")
    print(make_sample(p.ensure()), f"({time.time() - t:.1f}s)")
    cmd_build(p, a)
    print(f"\nDemo ready in {p.data}. Start the dashboard with:\n"
          f"  streamlit run app/dashboard.py -- --data-dir {p.data}")


COMMANDS = {
    "index": cmd_index, "download": cmd_download, "portal": cmd_portal, "parse": cmd_parse,
    "clean": cmd_clean, "link": cmd_link, "warehouse": cmd_warehouse, "quality": cmd_quality,
    "model": cmd_model, "export": cmd_export, "report": cmd_report,
    "score-review": cmd_score_review, "build": cmd_build, "collect": cmd_collect,
    "all": cmd_all, "demo": cmd_demo,
}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m nsq", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--data-dir", default=None, help="data folder (default: data/, demo: data_demo/)")
    ap.add_argument("--start-year", type=int, default=config.DEFAULT_START_YEAR)
    ap.add_argument("--limit", type=int, default=None, help="download only the N newest files (for a test run)")
    ap.add_argument("--llm", action="store_true", help="use an LLM for pages the table parser can't read")
    ap.add_argument("--horizon", type=int, default=1, help="model: predict a flag within N months")
    ap.add_argument("--top-k", type=int, default=20, help="model: size of the watch list")
    ap.add_argument("--strict", action="store_true", help="quality: exit with an error if a check fails")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    paths = config.get_paths(a.data_dir).ensure()
    COMMANDS[a.command](paths, a)


if __name__ == "__main__":
    main()
