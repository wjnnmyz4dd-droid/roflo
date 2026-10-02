"""Adversarial inputs to Finder / Validator / Arbiter / Risk, memory poisoning and promotion governance."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import math

import pytest
from conftest import NOW, trending_bars

from sentinel.arbiter import Arbiter
from sentinel.broker.sim import DEFAULT_SPECS
from sentinel.contracts import AccountSnapshot, ArbiterVerdict, Bar, Direction, GateVerdict, Position, Quote, ValidationVerdict
from sentinel.finder import Finder
from sentinel.journal import Journal
from sentinel.learning import Memory, MemoryRejected
from sentinel.promotion import STAGES, PromotionPipeline, PromotionRefused
from sentinel.risk import RiskEngine, RiskLimits
from sentinel.runtime import FakeClock
from sentinel.system import DEFAULT_COSTS
from sentinel.validator import Validator

H = 3600
GOLD = DEFAULT_SPECS["XAUUSD"]


def cand_and_bars(**kw):
    bars = trending_bars(**kw)
    c = Finder().scan("XAUUSD", bars, H, NOW, NOW)
    assert c is not None
    return c, bars


def validate(c, bars, now=NOW, **kw):
    return Validator(DEFAULT_COSTS).validate(c, bars, H, now, **kw)


def test_baseline_is_valid_and_approved():
    c, bars = cand_and_bars()
    v = validate(c, bars)
    assert v.verdict is ValidationVerdict.VALID
    assert Arbiter(measured_independence=0.8).decide(c, v, NOW).verdict is ArbiterVerdict.APPROVE_FOR_RISK_REVIEW


@pytest.mark.parametrize("corrupt,expected", [
    (lambda b: b[:-5] + [b[-3]] + b[-5:], ValidationVerdict.INVALID),  # duplicate/out-of-order bar
    (lambda b: b[:-10] + [dataclasses.replace(b[-10], low=-1.0)] + b[-9:], ValidationVerdict.INVALID),  # negative price
    (lambda b: b[:-10] + [dataclasses.replace(b[-10], close=0.0)] + b[-9:], ValidationVerdict.INVALID),  # zero price
    (lambda b: b[:-10] + [dataclasses.replace(b[-10], high=b[-10].low - 1)] + b[-9:], ValidationVerdict.INVALID),  # impossible OHLC
    (lambda b: b[:-10] + [dataclasses.replace(b[-10], close=b[-10].close * 3, high=b[-10].close * 3)] + b[-9:], ValidationVerdict.INVALID),  # bad tick
    (lambda b: b + [Bar("XAUUSD", NOW, 1, 1, 1, 1)], ValidationVerdict.INVALID),  # unfinished/future bar
    (lambda b: b[:-1], ValidationVerdict.INDETERMINATE),  # signal bar missing from validator's data
    (lambda b: [dataclasses.replace(x, instrument="USDJPY") if i == 50 else x for i, x in enumerate(b)], ValidationVerdict.INVALID),
])
def test_validator_rejects_corrupted_inputs(corrupt, expected):
    c, bars = cand_and_bars()
    v = validate(c, corrupt(list(bars)))
    assert v.verdict is expected
    assert Arbiter().decide(c, v, NOW).verdict is not ArbiterVerdict.APPROVE_FOR_RISK_REVIEW


def test_finder_never_uses_unfinished_bar():
    bars = trending_bars()
    future = bars + [Bar("XAUUSD", NOW, bars[-1].close, bars[-1].close + 500, bars[-1].close, bars[-1].close + 500)]
    a = Finder().scan("XAUUSD", bars, H, NOW, NOW)
    b = Finder().scan("XAUUSD", future, H, NOW, NOW)
    assert a.evidence == b.evidence and a.provenance.dataset_hash == b.provenance.dataset_hash


def test_stale_signal_and_closed_market_indeterminate():
    c, bars = cand_and_bars()
    assert validate(c, bars, now=NOW + 5 * H).verdict is ValidationVerdict.INDETERMINATE
    assert validate(c, bars, market_open=False).verdict is ValidationVerdict.INDETERMINATE


def test_spike_and_choppy_regime_falsified():
    c, bars = cand_and_bars(breakout=30.0)
    assert validate(c, bars).verdict is ValidationVerdict.INVALID
    c, bars = cand_and_bars(drift=0.0, noise=1.0, seed=3)
    assert validate(c, bars).verdict is ValidationVerdict.INVALID


def test_costs_destroy_edge_is_detected():
    from sentinel.validator import CostModel

    c, bars = cand_and_bars()
    v = Validator({"XAUUSD": CostModel(spread=8.0, commission_price=4.0, slippage=3.0)}).validate(c, bars, H, NOW)
    assert v.verdict is ValidationVerdict.INVALID and "costs" in v.reasons[0]


def test_insufficient_history_is_indeterminate():
    c, bars = cand_and_bars(n=140)
    assert validate(c, bars).verdict is ValidationVerdict.INDETERMINATE


def test_arbiter_rules_each_fire():
    c, bars = cand_and_bars()
    v = validate(c, bars)
    arb = Arbiter(measured_independence=0.8)
    assert arb.decide(c, v, c.expires_at).verdict is ArbiterVerdict.NO_TRADE
    assert arb.decide(c, None, NOW).verdict is ArbiterVerdict.INDETERMINATE
    assert arb.decide(c, dataclasses.replace(v, candidate_id="other"), NOW).verdict is ArbiterVerdict.INDETERMINATE
    assert arb.decide(c, dataclasses.replace(v, verdict=ValidationVerdict.INVALID), NOW).verdict is ArbiterVerdict.REJECT
    incomplete = dataclasses.replace(v, checks={k: x for k, x in v.checks.items() if k != "regime"})
    assert arb.decide(c, incomplete, NOW).verdict is ArbiterVerdict.INDETERMINATE
    contradictory = dataclasses.replace(v, checks={**v.checks, "cost_survival": {"passed": False}})
    assert arb.decide(c, contradictory, NOW).verdict is ArbiterVerdict.INDETERMINATE
    other_data = dataclasses.replace(v, data_sources=("deadbeef",))
    assert arb.decide(c, other_data, NOW).verdict is ArbiterVerdict.INDETERMINATE
    weak = dataclasses.replace(v, checks={**v.checks, "cost_survival": {**v.checks["cost_survival"], "mean_r": 0.07}})
    assert Arbiter(measured_independence=0.8).decide(c, weak, NOW).verdict is ArbiterVerdict.APPROVE_FOR_RISK_REVIEW
    assert Arbiter(measured_independence=0.2).decide(c, weak, NOW).verdict is ArbiterVerdict.NO_TRADE
    stripped = dataclasses.replace(c, evidence={})
    assert arb.decide(stripped, v, NOW).verdict is ArbiterVerdict.INDETERMINATE


def test_arbiter_has_no_market_or_broker_inputs():
    import inspect

    sig = inspect.signature(Arbiter.decide)
    assert list(sig.parameters) == ["self", "c", "v", "now"]


# ---------------------------------------------------------------- risk
def snap(eq=100_000.0, bal=100_000.0, positions=(), ts=NOW, margin=0.0):
    return AccountSnapshot(ts, ts, bal, eq, margin, eq - margin, "USD", tuple(positions), ()).with_hash()


def q(bid=2000.0, ask=2000.3, ts=NOW):
    return Quote("XAUUSD", bid, ask, ts)


def risk():
    return RiskEngine(RiskLimits(), 100_000.0)


def test_risk_sizes_to_budget_and_uses_broker_spec():
    d = risk().evaluate(snap(), [], DEFAULT_SPECS, GOLD, q(), Direction.LONG, 2000.3, 1990.0, NOW)
    assert d.verdict is GateVerdict.ALLOW
    loss = d.approved_volume * ((2000.3 - 1990.0) / GOLD.tick_size + 60) * GOLD.tick_value
    assert loss <= 250.0 + 1e-6


@pytest.mark.parametrize("kw,reason", [
    (dict(quote=None), "stale or invalid quote"),
    (dict(quote=Quote("XAUUSD", 2000, 1999, NOW)), "stale or invalid quote"),
    (dict(quote=Quote("XAUUSD", 2000, 2000.3, NOW - 100)), "stale or invalid quote"),
    (dict(stop=2005.0), "stop missing"),
    (dict(stop=2000.2), "stop missing"),
    (dict(quote=Quote("XAUUSD", 2000, 2003, NOW)), "spread"),
    (dict(snapshot=snap(ts=NOW - 60)), "snapshot age"),
    (dict(stop=float("nan")), "invalid entry/stop"),
])
def test_risk_blocks_bad_inputs(kw, reason):
    d = risk().evaluate(kw.get("snapshot", snap()), [], DEFAULT_SPECS, GOLD, kw.get("quote", q()), Direction.LONG,
                        2000.3, kw.get("stop", 1990.0), NOW)
    assert d.verdict is GateVerdict.BLOCK and reason in d.reasons[0]


def test_risk_halts_on_impossible_account_and_drawdown_and_streak():
    assert risk().account_check(snap(eq=-5, bal=-5), [], NOW).verdict is GateVerdict.HALT
    nan_snap = AccountSnapshot(NOW, NOW, 100_000.0, float("nan"), 0.0, 0.0, "USD", (), ())  # cannot even be hashed
    with pytest.raises(ValueError):
        nan_snap.with_hash()
    assert risk().account_check(nan_snap, [], NOW).verdict is GateVerdict.HALT
    assert risk().account_check(snap(eq=93_000, bal=93_000), [{"ts": 0, "profit": -7000, "commission": 0, "kind": "OUT:SL"}], NOW).verdict is GateVerdict.HALT
    losses = [{"ts": 0, "profit": -10, "commission": 0, "kind": "OUT:SL"}] * 5
    assert risk().account_check(snap(), losses, NOW).verdict is GateVerdict.HALT


def test_unprotected_or_unknown_position_blocks_new_risk():
    naked = Position("t", "XAUUSD", Direction.LONG, 0.01, 2000, None, None, NOW, None, 0.0)
    args = ([], DEFAULT_SPECS, DEFAULT_SPECS["BTCUSD"], Quote("BTCUSD", 60000, 60010, NOW), Direction.LONG, 60010.0, 59000.0, NOW)
    assert risk().evaluate(snap(), *args).verdict is GateVerdict.ALLOW  # control: same trade is fine without the naked position
    d = risk().evaluate(snap(positions=[naked]), *args)
    assert d.verdict is GateVerdict.BLOCK and "unbounded" in d.reasons[0]


def test_correlated_usd_bucket_and_concentration():
    p1 = Position("a", "USDJPY", Direction.SHORT, 0.1, 150, 151, None, NOW, "x", 0)  # short USD
    p2 = Position("b", "BTCUSD", Direction.LONG, 0.01, 60000, 59000, None, NOW, "y", 0)  # short USD
    d = risk().evaluate(snap(positions=[p1, p2]), [], DEFAULT_SPECS, GOLD, q(), Direction.LONG, 2000.3, 1990, NOW)
    assert d.verdict is GateVerdict.BLOCK and "USD" in d.reasons[0]


def test_margin_check():
    d = risk().evaluate(snap(margin=40_000), [], DEFAULT_SPECS, GOLD, q(), Direction.LONG, 2000.3, 1990.0, NOW)
    assert d.verdict is GateVerdict.BLOCK and "margin" in d.reasons[0]


# ---------------------------------------------------------------- learning / memory poisoning
@pytest.fixture
def mem(tmp_path):
    clock = FakeClock(NOW)
    tj = Journal(str(tmp_path / "trading.db"), clock=clock)
    tj.append("POSITION_CLOSED", "reconciler", {"ticket": "T1", "intent_id": "i1", "profit": -120.0, "commission": 5.0, "source": "broker_deal"})
    tj.append("CANDIDATE", "finder", {"candidate": {"candidate_id": "c1"}})
    return Memory(Journal(str(tmp_path / "mem.db"), clock=clock), tj, clock), tj, clock


def test_fact_only_from_verified_broker_truth(mem):
    m, tj, _ = mem
    fid = m.fact_from_journal(1)
    with pytest.raises(MemoryRejected, match="duplicate"):
        m.fact_from_journal(1)
    with pytest.raises(MemoryRejected, match="not broker truth"):
        m.fact_from_journal(2)
    with pytest.raises(MemoryRejected, match="only be created"):
        m.add("FACT", "trade T1 made +5000", [])
    with pytest.raises(MemoryRejected):
        m.fact_from_journal(999)
    assert fid == "fact-1"


def test_tampered_journal_blocks_fact_creation(tmp_path, mem):
    import sqlite3

    m, tj, _ = mem
    raw = sqlite3.connect(tj.path)
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("""UPDATE events SET payload='{"profit": 99999}' WHERE seq=1""")
    raw.commit()
    with pytest.raises(MemoryRejected, match="integrity"):
        m.fact_from_journal(1)


def test_future_dated_event_rejected(tmp_path):
    clock = FakeClock(NOW)
    tj = Journal(str(tmp_path / "t.db"), clock=FakeClock(NOW + 86400))
    tj.append("POSITION_CLOSED", "reconciler", {"profit": 1})
    m = Memory(Journal(str(tmp_path / "m.db"), clock=clock), tj, clock)
    with pytest.raises(MemoryRejected, match="future"):
        m.fact_from_journal(1)


def test_inference_never_becomes_fact_and_observations_cite_facts_only(mem):
    m, _, _ = mem
    fid = m.fact_from_journal(1)
    inf = m.add("INFERENCE", "the loss was caused by news", [fid])
    with pytest.raises(MemoryRejected):
        m.add("OBSERVATION", "laundering an inference", [inf])
    with pytest.raises(MemoryRejected):
        m.add("OBSERVATION", "hallucinated reference", ["fact-424242"])
    assert m.add("OBSERVATION", "one losing XAU trade", [fid]).startswith("observation")


def test_lessons_strengthen_weaken_retire_and_keep_provenance(tmp_path):
    clock = FakeClock(NOW)
    tj = Journal(str(tmp_path / "t.db"), clock=clock)
    for i in range(24):
        tj.append("POSITION_CLOSED", "reconciler", {"ticket": f"T{i}", "profit": 10.0 if i < 12 else -10.0, "commission": 0})
    m = Memory(Journal(str(tmp_path / "m.db"), clock=clock), tj, clock)
    facts = [m.fact_from_journal(i + 1) for i in range(24)]
    les = m.lesson("breakouts work in trending vol regime", {"vol": "mid"}, facts[:1])
    for f in facts[:12]:
        m.evidence(les, f, supports=True)
    assert m.lesson_status(les).status == "ACTIVE"
    with pytest.raises(MemoryRejected, match="already counted"):
        m.evidence(les, facts[0], supports=True)  # duplicated evidence cannot inflate a lesson
    for f in facts[12:]:
        m.evidence(les, f, supports=False)
    clock.advance(30 * 86400)
    assert m.lesson_status(les).status in ("WEAKENED", "RETIRED")
    clock.advance(400 * 86400)
    st = m.lesson_status(les)
    assert st.status == "WEAKENED" and st.support == 12 and st.contradict == 12  # history kept, not erased


def test_learning_output_is_only_a_proposal(mem):
    m, _, _ = mem
    fid = m.fact_from_journal(1)
    p = m.propose("widen stop to 2.5 ATR", {"FinderParams.stop_atr": 2.5}, "losses clustered at 2 ATR", [fid])
    assert set(p) == {"proposal_id", "title", "change", "refs"}
    import sentinel.learning as L

    src = open(L.__file__).read()
    for forbidden in ("FinderParams(", "RiskLimits(", "policy/", "open(", "send_order", "PermitIssuer"):
        assert forbidden not in src


# ---------------------------------------------------------------- promotion pipeline
def _op():
    key = "operator-secret-not-in-repo"
    return key, hashlib.sha256(key.encode()).hexdigest()


def test_promotion_requires_ordered_stages_registered_criteria_single_holdout_and_operator(tmp_path):
    key, fp = _op()
    pp = PromotionPipeline(Journal(str(tmp_path / "p.db")), fp)
    pid = pp.submit({"proposal_id": "p1", "change": {"x": 1}})
    with pytest.raises(PromotionRefused, match="register criteria"):
        pp.open_holdout(pid)
    h = pp.register_criteria(pid, {"min_expectancy_r": 0.05})
    with pytest.raises(PromotionRefused, match="cannot be changed"):
        pp.register_criteria(pid, {"min_expectancy_r": -1})
    with pytest.raises(PromotionRefused, match="out of order"):
        pp.record(pid, "BACKTEST", True, {}, h)
    with pytest.raises(PromotionRefused, match="other than the registered"):
        pp.record(pid, "RESEARCH_TEST", True, {}, "different-hash")
    for st in STAGES[:3]:
        pp.record(pid, st, True, {"stage": st}, h)
    with pytest.raises(PromotionRefused, match="holdout"):
        pp.record(pid, "OUT_OF_SAMPLE", True, {}, h)
    pp.open_holdout(pid)
    with pytest.raises(PromotionRefused, match="no reuse"):
        pp.open_holdout(pid)
    for st in STAGES[3:]:
        pp.record(pid, st, True, {"stage": st}, h)
    with pytest.raises(PromotionRefused, match="operator approval"):
        pp.promote(pid, None)
    with pytest.raises(PromotionRefused):
        pp.promote(pid, {"operator_key": "guess", "signature": "x"})
    sig = hmac.new(key.encode(), f"PROMOTE|{pid}|{h}".encode(), hashlib.sha256).hexdigest()
    assert pp.promote(pid, {"operator_key": key, "signature": sig}) == {"promoted": "p1", "live": False}


def test_failed_stage_ends_the_line(tmp_path):
    key, fp = _op()
    pp = PromotionPipeline(Journal(str(tmp_path / "p.db")), fp)
    pid = pp.submit({"proposal_id": "p2"})
    h = pp.register_criteria(pid, {})
    pp.record(pid, "RESEARCH_TEST", False, {}, h)
    with pytest.raises(PromotionRefused):
        pp.record(pid, "BACKTEST", True, {}, h)


def test_math_helpers():
    from sentinel.independence import jaccard, measure, phi

    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert math.isnan(phi([True], [True]))
    m = measure(("a",), ("a", "b"), True, [{"validator_valid": i % 2 == 0, "r": 1.0 if i % 2 == 0 else -1.0} for i in range(20)])
    assert m["validator_information_pbis"] > 0.9


def test_validator_analogs_do_not_overlap():
    c, bars = cand_and_bars()
    v = Validator(DEFAULT_COSTS)
    rs = v.analog_returns(bars[-1501:-1], c.direction, DEFAULT_COSTS["XAUUSD"])
    assert 0 < len(rs) <= 1500 // v.horizon + 1
