"""Pre-registered edge qualification. Usage: run_research.py dev | holdout | extras

dev      - development-period results only (daily < 2019, hourly first 365 days)
holdout  - opens the holdout ONCE through the promotion pipeline and evaluates the
           pre-registered criteria; refuses to run a second time
extras   - leakage probes, null model, independence, cost/parameter sensitivity on full sample
           (reported for information, never used to choose parameters)
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentinel.contracts import content_hash  # noqa: E402
from sentinel.finder import FinderParams  # noqa: E402
from sentinel.independence import measure  # noqa: E402
from sentinel.journal import Journal  # noqa: E402
from sentinel.promotion import PromotionPipeline, PromotionRefused  # noqa: E402
from sentinel.research import edge  # noqa: E402
from sentinel.research.data import load  # noqa: E402
from sentinel.system import DEFAULT_COSTS  # noqa: E402
from sentinel.validator import VALIDATOR_FEATURES  # noqa: E402
from sentinel.finder import FINDER_FEATURES  # noqa: E402

INSTR = ["XAUUSD", "USOIL", "USDJPY", "BTCUSD"]
SPLIT_D = int(datetime(2019, 1, 1, tzinfo=UTC).timestamp())
CRITERIA = {
    "pooled_trades_min": 100, "expectancy_r_min": 0.05, "t_stat_min": 2.0, "profit_factor_min": 1.15,
    "expectancy_2x_cost_gt": 0.0, "instruments_positive_min": 3, "walk_forward_positive_year_fraction_min": 0.60,
    "bootstrap_p5_gt": 0.0, "source": "research/PREREGISTRATION.md",
}
REPORTS = ROOT / "reports"


def data(iv):
    out = {}
    for i in INSTR:
        bars, meta = load(i, iv, str(ROOT / "data"))
        out[i] = (bars, meta)
    return out


def hourly_split(d):
    first = min(b[0].ts for b, _ in d.values())
    last = max(b[-1].ts for b, _ in d.values())
    return first + 365 * 86400, last


def evaluate(D, H, period):
    res = {"daily": {}, "hourly": {}}
    h_split, _ = hourly_split(H)
    for label, src, bs, s, e in (("daily", D, 86400, None, SPLIT_D), ("hourly", H, 3600, None, h_split)):
        if period == "holdout":
            s, e = e, None
        for variant, kw in (("finder", {}), ("finder+validator", {"use_validator": True}),
                            ("finder+validator+arbiter", {"use_validator": True, "use_arbiter": True})):
            per = {}
            pooled = []
            for ins, (bars, meta) in src.items():
                trades = edge.run(bars, ins, DEFAULT_COSTS[ins], start_ts=s, end_ts=e, bar_seconds=bs, **kw)
                rs = [t.r for t in trades]
                pooled += [(t.signal_ts, t.r) for t in trades]
                st = edge.stats(rs)
                st["expectancy_2x_cost"] = edge.stats([t.r for t in edge.run(bars, ins, edge.scale_cost(DEFAULT_COSTS[ins], 2), start_ts=s, end_ts=e, bar_seconds=bs, **kw)]).get("expectancy_r")
                st["data_sha256"] = meta["sha256"]
                per[ins] = st
            pooled.sort()
            prs = [r for _, r in pooled]
            res[label][variant] = {"per_instrument": per, "pooled": edge.stats(prs),
                                   "bootstrap_p5": round(edge.block_bootstrap_p5(prs), 4) if prs else None}
    return res


def walk_forward_years(D):
    years = {}
    for ins, (bars, _) in D.items():
        for t in edge.run(bars, ins, DEFAULT_COSTS[ins]):
            y = datetime.fromtimestamp(t.signal_ts, UTC).year
            years.setdefault(y, []).append(t.r)
    table = {y: edge.stats(r)["expectancy_r"] for y, r in sorted(years.items())}
    pos = sum(v > 0 for v in table.values())
    return {"by_year_expectancy_r": table, "positive_fraction": round(pos / len(table), 3)}


def judge(h, wf):
    p = h["daily"]["finder"]["pooled"]
    per = h["daily"]["finder"]["per_instrument"]
    ph2 = [v.get("expectancy_2x_cost") for v in per.values()]
    checks = {
        "pooled_trades_min": p["n"] >= CRITERIA["pooled_trades_min"],
        "expectancy_r_min": p.get("expectancy_r", -9) >= CRITERIA["expectancy_r_min"],
        "t_stat_min": (p.get("t_stat") or 0) >= CRITERIA["t_stat_min"],
        "profit_factor_min": (p.get("profit_factor") or 0) >= CRITERIA["profit_factor_min"],
        "expectancy_2x_cost_gt": sum(x or 0 for x in ph2) / len(ph2) > 0,
        "instruments_positive_min": sum((v.get("expectancy_r") or -1) > 0 for v in per.values()) >= CRITERIA["instruments_positive_min"],
        "walk_forward_positive_year_fraction_min": wf["positive_fraction"] >= CRITERIA["walk_forward_positive_year_fraction_min"],
        "bootstrap_p5_gt": (h["daily"]["finder"]["bootstrap_p5"] or -1) > 0,
    }
    return checks, all(checks.values())


def main(step):
    REPORTS.mkdir(exist_ok=True)
    D, H = data("1d"), data("1h")
    t0 = time.time()
    if step == "dev":
        out = {"period": "development", "criteria_hash": content_hash(CRITERIA), "results": evaluate(D, H, "dev")}
        json.dump(out, open(REPORTS / "edge_dev.json", "w"), indent=2)
    elif step == "holdout":
        pp = PromotionPipeline(Journal(str(REPORTS / "promotion.db")), operator_key_fingerprint=None)
        pid = "donchian_breakout_v1"
        try:
            pp.submit({"proposal_id": pid, "change": "initial hypothesis"})
            h = pp.register_criteria(pid, CRITERIA)
            pp.open_holdout(pid)
        except PromotionRefused as e:
            sys.exit(f"REFUSED: {e}")
        res = evaluate(D, H, "holdout")
        wf = walk_forward_years(D)
        checks, passed = judge(res, wf)
        out = {"period": "holdout", "criteria": CRITERIA, "criteria_hash": h, "results": res, "walk_forward": wf,
               "criteria_checks": checks, "EDGE_DEMONSTRATED": passed}
        json.dump(out, open(REPORTS / "edge_holdout.json", "w"), indent=2)
    elif step == "extras":
        x = {"leakage": {}, "null_random_entry": {}, "lookahead_positive_control": {}, "independence": {},
             "cost_stress_full_sample": {}, "parameter_sensitivity_full_sample": {}}
        recs_all = []
        for ins, (bars, meta) in D.items():
            c = DEFAULT_COSTS[ins]
            x["leakage"][ins] = edge.future_perturbation_check(bars, ins, c)
            x["null_random_entry"][ins] = edge.random_entry_null(bars, ins, c)
            x["lookahead_positive_control"][ins] = edge.lookahead_sensitivity(bars, ins, c)
            recs = edge.validator_labels(bars, ins, c)
            recs_all += recs
            x["independence"][ins] = measure(FINDER_FEATURES, VALIDATOR_FEATURES, True, recs)
            x["cost_stress_full_sample"][ins] = {k: edge.stats([t.r for t in edge.run(bars, ins, edge.scale_cost(c, k))]).get("expectancy_r")
                                                 for k in (0, 1, 2, 3, 5)}
            sens = {}
            for ch in (10, 20, 55):
                for sa in (1.5, 2.0, 3.0):
                    for hz in (5, 10, 20):
                        p = FinderParams(channel=ch, stop_atr=sa, horizon_bars=hz)
                        sens[f"ch{ch}_stop{sa}_h{hz}"] = edge.stats([t.r for t in edge.run(bars, ins, c, p=p)]).get("expectancy_r")
            x["parameter_sensitivity_full_sample"][ins] = sens
        x["independence"]["pooled"] = measure(FINDER_FEATURES, VALIDATOR_FEATURES, True, recs_all)
        json.dump(x, open(REPORTS / "edge_extras.json", "w"), indent=2)
    print(f"{step} done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1])
