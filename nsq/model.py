"""Stage 7: which factories are likely to be flagged again?

Framing (be precise about this in interviews):
* The universe is factories that have been flagged at least once before.
  Factories never flagged are not in CDSCO's lists, so nothing can be said
  about them. This is a RE-FLAG model, not a "which factory is bad" model.
* One row per factory per month. Features use ONLY months before that month,
  so the model never peeks at the future.
* Label: flagged in that month (or within --horizon months).
* Train on earlier months, test on the last 12 months only.
* Baseline to beat: rank factories by flags in the last 6 months.
* Metric: precision@20 - of the 20 factories on the watch list each month,
  how many were actually flagged. Compared with the baseline every month.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score

from . import config
from .warehouse import query

log = logging.getLogger(__name__)

FEATURES = [
    "flags_1m", "flags_3m", "flags_6m", "flags_12m", "flags_total",
    "months_since_last", "months_since_first", "drugs_total",
    "injection_share", "sterile_fail_6m", "assay_diss_6m",
    "district_flags_6m", "national_flags_1m",
]


def _site_month_matrix(f: pd.DataFrame, months: pd.PeriodIndex, value=None) -> pd.DataFrame:
    g = f.groupby(["site_id", "period"]).size() if value is None else f.groupby(["site_id", "period"])[value].sum()
    m = g.unstack(fill_value=0)
    return m.reindex(columns=months, fill_value=0)


def build_panel(paths: config.Paths, horizon: int = 1) -> tuple[pd.DataFrame, pd.PeriodIndex]:
    f = query(paths, """
        SELECT site_id, report_month, drug_generic, drug_form, reason_categories,
               COALESCE(mfr_district, 'unknown') AS district
        FROM fct_flags WHERE NOT is_spurious AND site_id IS NOT NULL""")
    f["period"] = pd.to_datetime(f["report_month"]).dt.to_period("M")
    months = pd.period_range(f["period"].min(), f["period"].max() + horizon, freq="M")
    f["is_inj"] = f["drug_form"].isin(["injection", "eye drops"]).astype(int)
    rc = f["reason_categories"].fillna("")
    f["sterile_fail"] = rc.str.contains("sterility|particulate|endotoxin").astype(int)
    f["assay_diss"] = rc.str.contains("assay|dissolution").astype(int)

    cnt = _site_month_matrix(f, months)
    inj = _site_month_matrix(f, months, "is_inj").reindex(cnt.index, fill_value=0)
    ster = _site_month_matrix(f, months, "sterile_fail").reindex(cnt.index, fill_value=0)
    ad = _site_month_matrix(f, months, "assay_diss").reindex(cnt.index, fill_value=0)
    district = f.groupby("site_id")["district"].agg(lambda s: s.mode().iat[0])

    def past_sum(mat: pd.DataFrame, window: int | None) -> pd.DataFrame:
        shifted = mat.shift(1, axis=1).fillna(0)  # only months BEFORE t
        if window is None:
            return shifted.cumsum(axis=1)
        return shifted.T.rolling(window, min_periods=1).sum().T

    feats = {
        "flags_1m": past_sum(cnt, 1), "flags_3m": past_sum(cnt, 3), "flags_6m": past_sum(cnt, 6),
        "flags_12m": past_sum(cnt, 12), "flags_total": past_sum(cnt, None),
        "inj_total": past_sum(inj, None), "sterile_fail_6m": past_sum(ster, 6),
        "assay_diss_6m": past_sum(ad, 6),
    }
    # months since first / last flag (before t)
    idx = np.arange(len(months))
    flagged = cnt.values > 0
    last_seen = np.full(cnt.shape, np.nan)
    first_seen = np.full(cnt.shape, np.nan)
    for r in range(cnt.shape[0]):
        last = np.nan
        first = np.nan
        for c in range(cnt.shape[1]):
            last_seen[r, c] = c - last if not np.isnan(last) else np.nan
            first_seen[r, c] = c - first if not np.isnan(first) else np.nan
            if flagged[r, c]:
                last = c
                if np.isnan(first):
                    first = c
    # distinct drugs before t
    first_drug = f.groupby(["site_id", "drug_generic"])["period"].min().reset_index()
    drugs = _site_month_matrix(first_drug, months).reindex(cnt.index, fill_value=0)
    feats["drugs_total"] = past_sum(drugs, None)

    long = []
    for name, mat in feats.items():
        long.append(mat.stack().rename(name))
    panel = pd.concat(long, axis=1).reset_index().rename(columns={"level_1": "period"})
    panel = panel.rename(columns={panel.columns[1]: "period"})
    panel["months_since_last"] = last_seen.reshape(-1)
    panel["months_since_first"] = first_seen.reshape(-1)
    panel["injection_share"] = (panel["inj_total"] / panel["flags_total"].replace(0, np.nan)).fillna(0)

    # neighbourhood and national pressure
    panel["district"] = panel["site_id"].map(district)
    dist_month = (f.groupby(["district", "period"]).size().unstack(fill_value=0)
                  .reindex(columns=months, fill_value=0))
    dist6 = past_sum(dist_month, 6).stack().rename("district_flags_6m").reset_index()
    dist6.columns = ["district", "period", "district_flags_6m"]
    panel = panel.merge(dist6, on=["district", "period"], how="left")
    panel["district_flags_6m"] = panel["district_flags_6m"] - panel["flags_6m"]  # others only
    nat = cnt.sum(axis=0)
    panel["national_flags_1m"] = panel["period"].map(nat.shift(1).fillna(0))

    # label: flagged in [t, t + horizon - 1]
    fut = sum(cnt.shift(-k, axis=1).fillna(0) for k in range(horizon)) > 0
    lab = fut.stack().rename("label").reset_index()
    lab.columns = ["site_id", "period", "label"]
    panel = panel.merge(lab, on=["site_id", "period"], how="left")
    # Keep months after the site's first flag (the re-flag universe) and
    # months whose label we can fully observe.
    last_obs = f["period"].max()
    panel = panel[panel["months_since_first"].notna()]
    panel["label_known"] = panel["period"] + (horizon - 1) <= last_obs
    panel["label"] = panel["label"].astype(int)
    return panel.reset_index(drop=True), months


def precision_at_k(df: pd.DataFrame, score: str, k: int) -> pd.Series:
    def one(g):
        top = g.sort_values([score, "flags_12m"], ascending=False).head(k)
        return top["label"].mean()
    return df.groupby("period").apply(one, include_groups=False)


MIN_MONTHS = 12  # months with a known label; fewer than this and a test split means nothing


def _months(paths: config.Paths) -> list:
    try:
        return query(paths, "SELECT DISTINCT report_month FROM fct_flags")["report_month"].tolist()
    except Exception:
        return []


def _enough_rows(paths: config.Paths) -> bool:
    return len(_months(paths)) >= MIN_MONTHS


def _skip(paths: config.Paths, reason: str) -> dict:
    """Too little data for an honest test: say so and remove old model files."""
    paths.exports.mkdir(parents=True, exist_ok=True)
    for f in ("model_monthly.csv", "watch_list.csv"):
        (paths.exports / f).unlink(missing_ok=True)
    metrics = {"dataset": config.dataset_label(paths), "skipped": True,
               "reason": f"Model skipped: {reason}"}
    (paths.exports / "model_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    log.info(metrics["reason"])
    return metrics


def train_and_evaluate(paths: config.Paths, horizon: int = 1, top_k: int = 20,
                       test_months: int = 12) -> dict:
    panel, months = build_panel(paths, horizon) if _enough_rows(paths) else (pd.DataFrame(), None)
    known = panel[panel["label_known"]] if len(panel) else panel
    periods = sorted(known["period"].unique()) if len(known) else []
    if len(periods) < MIN_MONTHS:
        return _skip(paths, f"needs at least {MIN_MONTHS} months of alerts, the data has "
                            f"{len(_months(paths))}. Run a full collect (no --limit), then build again.")
    if len(periods) < test_months + 12:
        test_months = max(3, len(periods) // 4)
    test_start = periods[-test_months]
    train = known[(known["period"] < test_start) & (known["months_since_first"] >= 1)]
    test = known[known["period"] >= test_start].copy()
    if train["label"].nunique() < 2 or test["label"].nunique() < 2:
        return _skip(paths, "not enough repeat flags yet to learn from. Collect more months.")

    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                           l2_regularization=1.0, random_state=0)
    model.fit(train[FEATURES], train["label"])
    test["model_score"] = model.predict_proba(test[FEATURES])[:, 1]
    # Baseline: recent history only (ties broken by the year's count)
    test["baseline_score"] = test["flags_6m"] + test["flags_12m"] / 100

    p_model = precision_at_k(test, "model_score", top_k)
    p_base = precision_at_k(test, "baseline_score", top_k)
    ap_model = average_precision_score(test["label"], test["model_score"])
    ap_base = average_precision_score(test["label"], test["baseline_score"])
    imp = permutation_importance(model, test[FEATURES], test["label"], n_repeats=5,
                                 random_state=0, scoring="average_precision")
    importance = (pd.Series(imp.importances_mean, index=FEATURES)
                  .sort_values(ascending=False).round(4).to_dict())

    monthly = pd.DataFrame({"period": p_model.index.astype(str), "model_precision_at_k": p_model.values,
                            "baseline_precision_at_k": p_base.reindex(p_model.index).values,
                            "flagged_sites": test.groupby("period")["label"].sum().reindex(p_model.index).values,
                            "sites_scored": test.groupby("period").size().reindex(p_model.index).values})

    # Final model on all known months, then score the month after the data ends.
    final = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                           l2_regularization=1.0, random_state=0)
    final.fit(known[FEATURES], known["label"])
    next_period = panel["period"].max()
    latest = panel[panel["period"] == next_period].copy()
    latest["risk_score"] = final.predict_proba(latest[FEATURES])[:, 1]
    sites = query(paths, "SELECT site_id, company_name, state, district, total_flags, last_flag_month FROM dim_site")
    watch = (latest.drop(columns=["district"]).merge(sites, on="site_id", how="left")
             .sort_values("risk_score", ascending=False)
             [["site_id", "company_name", "state", "district", "risk_score", "flags_6m",
               "flags_12m", "months_since_last", "total_flags", "last_flag_month"]])
    watch.insert(0, "rank", range(1, len(watch) + 1))
    watch["scored_for_month"] = str(next_period)

    metrics = {
        "dataset": config.dataset_label(paths),
        "horizon_months": horizon, "top_k": top_k,
        "train_rows": int(len(train)), "test_rows": int(len(test)),
        "test_period": f"{periods[-test_months]} to {periods[-1]}",
        "test_months": int(test_months),
        "base_rate": round(float(test["label"].mean()), 4),
        "model_precision_at_k": round(float(p_model.mean()), 4),
        "baseline_precision_at_k": round(float(p_base.mean()), 4),
        "months_model_beats_baseline": int((p_model > p_base).sum()),
        "months_tied": int((p_model == p_base).sum()),
        "model_average_precision": round(float(ap_model), 4),
        "baseline_average_precision": round(float(ap_base), 4),
        "feature_importance": importance,
        "watch_list_month": str(next_period),
        "caveat": ("Re-flag model on factories already flagged at least once. CDSCO samples are not "
                   "random, so this predicts where regulators find failures, not true quality."),
    }
    paths.exports.mkdir(parents=True, exist_ok=True)
    (paths.exports / "model_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    monthly.to_csv(paths.exports / "model_monthly.csv", index=False)
    watch.to_csv(paths.exports / "watch_list.csv", index=False)
    metrics["monthly"] = monthly
    log.info("precision@%d model %.3f vs baseline %.3f", top_k, metrics["model_precision_at_k"],
             metrics["baseline_precision_at_k"])
    return metrics
