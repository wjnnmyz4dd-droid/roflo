"""Phase-2 lineages per research/PREREGISTRATION_PHASE2.md.

Usage:
  run_phase2.py devval            -> dev + validation stats and gate decisions (no holdout data touched)
  run_phase2.py holdout L2|L3     -> only if the lineage passed its validation gate; opens the holdout
                                     ONCE via the promotion journal and evaluates the frozen criteria
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sentinel.journal import Journal  # noqa: E402
from sentinel.promotion import PromotionPipeline, PromotionRefused  # noqa: E402
from sentinel.research import edge  # noqa: E402
from sentinel.research.data import load  # noqa: E402
from sentinel.research.lineages import LEGS, fx_local_hours, tsmom  # noqa: E402
from sentinel.system import DEFAULT_COSTS  # noqa: E402

REPORTS = ROOT / "reports"
NON_BTC = ["XAUUSD", "USOIL", "USDJPY"]
T2011 = int(datetime(2011, 1, 1, tzinfo=UTC).timestamp())
T2019 = int(datetime(2019, 1, 1, tzinfo=UTC).timestamp())
CRIT = {
    "L2": {"n_min": 60, "expectancy_r_min": 0.10, "t_min": 2.0, "pf_min": 1.20, "exp_2x_gt": 0.0,
           "instruments_positive_min": 2, "bootstrap_p5_gt": 0.0, "gate": "validation expectancy > 0 and n >= 60 (non-BTC pooled)"},
    "L3": {"n_min": 150, "exp_1x_gt": 0.0, "exp_1_5x_gt": 0.0, "t_min": 2.0, "pf_min": 1.10, "legs_nonneg": True,
           "bootstrap_p5_gt": 0.0, "gate": "validation expectancy > 0 and n >= 100"},
}
PROVENANCE = "PROXY / RESEARCH-ONLY (Yahoo GC=F, CL=F, JPY=X, BTC-USD); not broker data"


def daily():
    return {i: load(i, "1d", str(ROOT / "data")) for i in NON_BTC + ["BTCUSD"]}


def jpy_hourly():
    bars, meta = load("USDJPY", "1h", str(ROOT / "data"))
    t0, t1 = bars[0].ts, bars[-1].ts
    third = (t1 - t0) // 3
    return bars, meta, (t0, t0 + third, t0 + 2 * third, t1 + 1)


def l2(D, s, e, mult=1.0, lookback=252):
    per, pooled = {}, []
    for ins, (bars, meta) in D.items():
        tr = tsmom(bars, ins, DEFAULT_COSTS[ins], cost_mult=mult, start_ts=s, end_ts=e, lookback=lookback)
        per[ins] = edge.stats([t.r for t in tr])
        if ins in NON_BTC:
            pooled += tr
    pooled.sort(key=lambda t: t.signal_ts)
    return per, pooled


def l3(bars, s, e, mult=1.0, legs=None):
    return fx_local_hours(bars, DEFAULT_COSTS["USDJPY"], cost_mult=mult, start_ts=s, end_ts=e, legs=legs)


def summary(trades):
    rs = [t.r for t in trades]
    st = edge.stats(rs)
    st["bootstrap_p5"] = round(edge.block_bootstrap_p5(rs), 4) if len(rs) >= 40 else None
    return st


def devval():
    D = daily()
    out = {"provenance": PROVENANCE, "data_sha256": {i: m["sha256"] for i, (_, m) in D.items()}, "L2": {}, "L3": {}}
    for name, (s, e) in {"development": (None, T2011), "validation": (T2011, T2019)}.items():
        per, pooled = l2(D, s, e)
        out["L2"][name] = {"per_instrument": per, "pooled_non_btc": summary(pooled),
                           "cost_stress_pooled": {str(k): edge.stats([t.r for t in l2(D, s, e, k)[1]]).get("expectancy_r") for k in (1, 1.5, 2, 3)},
                           "sensitivity_lookback": {str(lb): edge.stats([t.r for t in l2(D, s, e, 1.0, lb)[1]]).get("expectancy_r") for lb in (126, 189, 252)}}
    v = out["L2"]["validation"]["pooled_non_btc"]
    out["L2"]["gate_passed"] = bool(v["n"] >= 60 and v["expectancy_r"] > 0)

    bars, meta, (t0, t1, t2, t3) = jpy_hourly()
    out["L3"]["data_sha256"] = meta["sha256"]
    out["L3"]["splits_utc"] = [t0, t1, t2, t3]
    for name, (s, e) in {"development": (t0, t1), "validation": (t1, t2)}.items():
        tr = l3(bars, s, e)
        out["L3"][name] = {"pooled": summary(tr),
                           "legs": {leg: edge.stats([t.r for t in tr if t.leg == leg]) for leg in LEGS},
                           "cost_stress": {str(k): edge.stats([t.r for t in l3(bars, s, e, k)]).get("expectancy_r") for k in (0, 1, 1.5, 2, 3)},
                           "sensitivity_pm1h": {
                               "shift_-1h": edge.stats([t.r for t in l3(bars, s, e, legs={"T": (23 - 24, 6, 1), "N": (12, 19, -1)})]).get("expectancy_r"),
                               "shift_+1h": edge.stats([t.r for t in l3(bars, s, e, legs={"T": (1, 8, 1), "N": (14, 21, -1)})]).get("expectancy_r")}}
    v = out["L3"]["validation"]["pooled"]
    out["L3"]["gate_passed"] = bool(v["n"] >= 100 and v["expectancy_r"] > 0)
    (REPORTS / "phase2_devval.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: out[k].get("gate_passed") for k in ("L2", "L3")}))


def holdout(lineage):
    dv = json.loads((REPORTS / "phase2_devval.json").read_text())
    if not dv[lineage]["gate_passed"]:
        sys.exit(f"REFUSED: {lineage} failed its validation gate; holdout stays closed (pre-registered)")
    pp = PromotionPipeline(Journal(str(REPORTS / "promotion.db")), operator_key_fingerprint=None)
    pid = {"L2": "L2_tsmom_12m_v1", "L3": "L3_fx_local_hours_v1"}[lineage]
    try:
        pp.submit({"proposal_id": pid, "preregistration": "research/PREREGISTRATION_PHASE2.md"})
        h = pp.register_criteria(pid, CRIT[lineage])
        pp.open_holdout(pid)
    except PromotionRefused as e:
        sys.exit(f"REFUSED: {e}")
    C = CRIT[lineage]
    if lineage == "L2":
        D = daily()
        per, pooled = l2(D, T2019, None)
        st = summary(pooled)
        e2 = edge.stats([t.r for t in l2(D, T2019, None, 2.0)[1]]).get("expectancy_r")
        checks = {"n_min": st["n"] >= C["n_min"], "expectancy_r_min": st["expectancy_r"] >= C["expectancy_r_min"],
                  "t_min": (st.get("t_stat") or 0) >= C["t_min"], "pf_min": (st.get("profit_factor") or 0) >= C["pf_min"],
                  "exp_2x_gt": e2 > 0, "instruments_positive_min": sum((per[i].get("expectancy_r") or -1) > 0 for i in NON_BTC) >= 2,
                  "bootstrap_p5_gt": (st.get("bootstrap_p5") or -1) > 0}
        res = {"per_instrument": per, "pooled_non_btc": st, "exp_2x": e2,
               "cost_stress": {str(k): edge.stats([t.r for t in l2(D, T2019, None, k)[1]]).get("expectancy_r") for k in (1, 1.5, 2, 3)}}
    else:
        bars, meta, (t0, t1, t2, t3) = jpy_hourly()
        tr = l3(bars, t2, t3)
        st = summary(tr)
        legs = {leg: edge.stats([t.r for t in tr if t.leg == leg]) for leg in LEGS}
        e15 = edge.stats([t.r for t in l3(bars, t2, t3, 1.5)]).get("expectancy_r")
        checks = {"n_min": st["n"] >= C["n_min"], "exp_1x_gt": st["expectancy_r"] > 0, "exp_1_5x_gt": e15 > 0,
                  "t_min": (st.get("t_stat") or 0) >= C["t_min"], "pf_min": (st.get("profit_factor") or 0) >= C["pf_min"],
                  "legs_nonneg": all((v.get("expectancy_r") or -1) >= 0 for v in legs.values()),
                  "bootstrap_p5_gt": (st.get("bootstrap_p5") or -1) > 0}
        res = {"pooled": st, "legs": legs, "exp_1_5x": e15,
               "cost_stress": {str(k): edge.stats([t.r for t in l3(bars, t2, t3, k)]).get("expectancy_r") for k in (0, 1, 1.5, 2, 3)}}
    out = {"lineage": pid, "provenance": PROVENANCE, "criteria": C, "criteria_hash": h, "results": res, "checks": checks,
           "PASSED": all(checks.values())}
    (REPORTS / f"phase2_holdout_{lineage}.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({"lineage": pid, "PASSED": out["PASSED"], "checks": checks}))


if __name__ == "__main__":
    devval() if sys.argv[1] == "devval" else holdout(sys.argv[2])
