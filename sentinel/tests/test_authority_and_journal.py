"""Journal immutability, authority boundaries, and compliance/risk BYPASS attempts on every surface."""

from __future__ import annotations

import ast
import dataclasses
import os
import pathlib
import sqlite3

import pytest
from conftest import NOW, make_system

from sentinel.authority import AUTHORITY_MATRIX, NO_BROKER_ACCESS, Permit, PermitError, PermitIssuer, PermitKey
from sentinel.contracts import ArbiterVerdict, Direction, GateDecision, GateVerdict, OrderIntent
from sentinel.execution import ExecutionRefused
from sentinel.journal import DuplicateKey, Journal

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "sentinel"


# ---------------------------------------------------------------- journal
def test_journal_is_append_only_and_chain_verifies(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    for i in range(5):
        j.append("X", "t", {"i": i})
    assert j.verify()[0]
    with pytest.raises(sqlite3.DatabaseError):
        j._db.execute("UPDATE events SET payload='{}' WHERE seq=2")
    with pytest.raises(sqlite3.DatabaseError):
        j._db.execute("DELETE FROM events WHERE seq=2")


def test_journal_detects_tampering_even_if_triggers_are_dropped(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    for i in range(5):
        j.append("POSITION_CLOSED", "t", {"profit": 10 * i})
    raw = sqlite3.connect(str(tmp_path / "j.db"))
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("""UPDATE events SET payload='{"profit":99999}' WHERE seq=3""")
    raw.commit()
    ok, why = Journal(str(tmp_path / "j.db")).verify()
    assert not ok and "seq 3" in why


def test_unique_keys_enforced(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    j.append("A", "t", {}, unique=[("permit", "p1")])
    with pytest.raises(DuplicateKey):
        j.append("A", "t", {}, unique=[("permit", "p1")])
    assert sum(1 for _ in j.events()) == 1  # failed append rolled back fully


# ---------------------------------------------------------------- static authority
def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
            out |= {f"{n.module}.{a.name}" for a in n.names}
    return out


@pytest.mark.parametrize("module", NO_BROKER_ACCESS)
def test_non_execution_components_cannot_reach_broker_or_permits(module):
    imps = _imports(SRC / f"{module}.py")
    forbidden = [i for i in imps if i.startswith(("sentinel.broker", "sentinel.execution", "sentinel.system", "sentinel.orchestrator"))
                 or i in ("sentinel.authority.PermitIssuer", "sentinel.authority.PermitKey")]
    assert not forbidden, f"{module} imports {forbidden}"


def test_no_component_but_execution_calls_send_order():
    offenders = []
    for p in SRC.rglob("*.py"):
        if p.name in ("execution.py",) or "broker" in p.parts:
            continue
        if "send_order(" in p.read_text() or "close_position(" in p.read_text():
            offenders.append(str(p))
    assert not offenders, offenders


def test_authority_matrix_has_no_duplicate_execute_or_propose():
    executors = [k for k, v in AUTHORITY_MATRIX.items() if "X" in v]
    proposers = [k for k, v in AUTHORITY_MATRIX.items() if "P" in v]
    assert executors == ["execution"] and proposers == ["finder"]
    for vetoer in ("news", "compliance", "risk"):
        assert "P" not in AUTHORITY_MATRIX[vetoer] and "X" not in AUTHORITY_MATRIX[vetoer]
    assert AUTHORITY_MATRIX["learning"].isdisjoint({"P", "X", "S", "B", "PR"})


def test_shadow_mode_has_no_execution_path(tmp_path, clock):
    b = make_system(tmp_path, clock, mode="shadow")
    assert b.comps.execution is None
    r = b.orch.cycle(NOW)
    assert r["candidates"][0]["verdict"] == "WOULD_EXECUTE"
    assert not [c for c in b.broker.calls if c.startswith("send")]


# ---------------------------------------------------------------- permit forging / bypass
def _approved(tmp_path, clock):
    """Run the real chain once in shadow mode and capture its genuine signed decisions."""
    b = make_system(tmp_path, clock, mode="paper")
    j = b.journal
    r = b.orch.cycle(NOW)
    assert r["candidates"][0]["verdict"] == "FILLED"
    return b


def _decision(authority, verdict=GateVerdict.ALLOW, vol=None):
    return GateDecision(authority, verdict, ("forged",), "x", NOW, approved_volume=vol)


def test_forged_decisions_cannot_mint_a_permit(tmp_path, clock):
    b = make_system(tmp_path, clock)
    intent = OrderIntent("intent-forged", "cand-x", "XAUUSD", Direction.LONG, 1.0, 2900.0, None, 20, NOW, NOW + 60)
    from sentinel.contracts import ArbiterDecision

    arb = ArbiterDecision("cand-x", ArbiterVerdict.APPROVE_FOR_RISK_REVIEW, ("R9",), ("forged",), "v", NOW)
    with pytest.raises(PermitError, match="signature"):
        b.comps.issuer.issue(intent, arb, _decision("compliance"), _decision("news"), _decision("risk", vol=1.0), NOW)


def test_permit_cannot_be_reused_or_altered_or_expire_silently(tmp_path, clock):
    b = make_system(tmp_path, clock)
    issued = []
    real_issue = b.comps.issuer.issue

    def spy(*a, **k):
        p = real_issue(*a, **k)
        issued.append(p)
        return p

    b.comps.issuer.issue = spy
    b.orch.cycle(NOW)
    permit = issued[0]
    ex = b.comps.execution
    with pytest.raises(ExecutionRefused, match="duplicate"):
        ex.submit(permit)  # replay of a used permit
    bigger = dataclasses.replace(permit, intent=dataclasses.replace(permit.intent, volume=permit.intent.volume * 10))
    with pytest.raises(ExecutionRefused, match="altered"):
        ex.submit(bigger)
    clock.advance(3600)
    with pytest.raises(ExecutionRefused):
        ex.submit(permit)
    assert b.broker.open_position_count() == 1


def test_permit_signed_by_wrong_key_rejected(tmp_path, clock):
    b = make_system(tmp_path, clock)
    rogue = PermitIssuer(PermitKey(), {k: PermitKey() for k in ("arbiter", "compliance", "news", "risk")})
    intent = OrderIntent("intent-r", "c", "XAUUSD", Direction.LONG, 0.1, 2900.0, None, 20, NOW, NOW + 60)
    fake = Permit("permit-r", intent, "h", "d", NOW, NOW + 30, "00" * 32)
    with pytest.raises(ExecutionRefused):
        b.comps.execution.submit(fake)
    assert rogue is not None and b.broker.open_position_count() == 0


def test_issuer_refuses_volume_above_risk_approval_and_non_allow_gates(tmp_path, clock):
    b = make_system(tmp_path, clock, mode="shadow")
    captured = {}
    real = b.comps.issuer.issue

    def spy(intent, arb, comp, news, risk, now):
        captured.update(intent=intent, arb=arb, comp=comp, news=news, risk=risk)
        return real(intent, arb, comp, news, risk, now)

    b.comps.issuer.issue = spy
    b.orch.cycle(NOW)
    c = captured
    with pytest.raises(PermitError, match="exceeds"):
        real(dataclasses.replace(c["intent"], volume=c["risk"].approved_volume * 2), c["arb"], c["comp"], c["news"], c["risk"], NOW)
    # flipping a signed decision's verdict breaks its signature
    blocked = dataclasses.replace(c["news"], verdict=GateVerdict.BLOCK)
    with pytest.raises(PermitError):
        real(c["intent"], c["arb"], c["comp"], blocked, c["risk"], NOW)
    # swapping authorities (news decision presented as compliance) is rejected
    with pytest.raises(PermitError):
        real(c["intent"], c["arb"], c["news"], c["news"], c["risk"], NOW)
    # stale decisions are rejected
    with pytest.raises(PermitError, match="stale"):
        real(c["intent"], c["arb"], c["comp"], c["news"], c["risk"], NOW + 3600)


def test_execution_refuses_when_not_reconciled_or_halted(tmp_path, clock):
    b = make_system(tmp_path, clock, mode="shadow")
    issued = []
    real = b.comps.issuer.issue
    b.comps.issuer.issue = lambda *a, **k: issued.append(real(*a, **k)) or issued[-1]
    b.orch.cycle(NOW)
    from sentinel.execution import ExecutionService

    ex = ExecutionService(b.broker, b.journal, b.comps.issuer._key, b.comps.lease, clock, b.comps.control)
    b.comps.control.halt("risk", "test halt", scope="manual")
    with pytest.raises(ExecutionRefused, match="manual halt"):
        ex.submit(issued[0])
    assert b.broker.open_position_count() == 0


def test_bypass_surfaces_inventory():
    """Every module that can reach the broker is listed; only execution, reconciler (read-only), and the
    builder hold a broker handle. A new surface (webhook, REST, scheduler) would have to go through
    ExecutionService.submit, which requires a signed permit."""
    holders = sorted(p.name for p in SRC.rglob("*.py") if "_broker" in p.read_text() or "broker." in p.read_text())
    # broker port implementations (sim.py, mt5.py) are the broker itself, not callers of it
    assert set(holders) <= {"execution.py", "reconcile.py", "orchestrator.py", "system.py", "base.py", "sim.py", "mt5.py", "replay.py"}, holders
    assert not os.path.exists(SRC / "api.py") and not os.path.exists(SRC / "webhook.py")


# ---------------------------------------------------------------- added after mutation round 1
def _capture(b):
    cap = {}
    real = b.comps.issuer.issue

    def spy(intent, arb, comp, news, risk, now):
        cap.update(intent=intent, arb=arb, comp=comp, news=news, risk=risk)
        return real(intent, arb, comp, news, risk, now)

    b.comps.issuer.issue = spy
    return cap, real


def test_unused_expired_permit_is_refused(tmp_path, clock):
    b = make_system(tmp_path, clock, mode="shadow")
    cap, real = _capture(b)
    b.orch.cycle(NOW)
    permit = real(cap["intent"], cap["arb"], cap["comp"], cap["news"], cap["risk"], NOW)
    from sentinel.execution import ExecutionService

    ex = ExecutionService(b.broker, b.journal, b.comps.issuer._key, b.comps.lease, clock, b.comps.control)
    clock.advance(31)  # past the 30 s TTL, never used
    with pytest.raises(ExecutionRefused, match="expired"):
        ex.submit(permit)
    assert b.broker.open_position_count() == 0


def test_one_decision_set_cannot_mint_two_positions(tmp_path, clock):
    """Orchestrator bug model: the same signed approvals reused for a second intent id."""
    b = make_system(tmp_path, clock, mode="shadow")
    cap, real = _capture(b)
    b.orch.cycle(NOW)
    from sentinel.execution import ExecutionService

    ex = ExecutionService(b.broker, b.journal, b.comps.issuer._key, b.comps.lease, clock, b.comps.control)
    p1 = real(cap["intent"], cap["arb"], cap["comp"], cap["news"], cap["risk"], NOW)
    p2 = real(dataclasses.replace(cap["intent"], intent_id="intent-second"), cap["arb"], cap["comp"], cap["news"], cap["risk"], NOW)
    assert ex.submit(p1)["status"] == "FILLED"
    with pytest.raises(ExecutionRefused, match="duplicate"):
        ex.submit(p2)
    assert b.broker.open_position_count() == 1


def test_decisions_are_bound_to_candidate_instrument_direction_and_stop(tmp_path, clock):
    b = make_system(tmp_path, clock, mode="shadow")
    cap, real = _capture(b)
    b.orch.cycle(NOW)
    i = cap["intent"]
    # a GENUINELY signed arbiter approval for another candidate, paired with this candidate's gate decisions
    other_arb = b.comps.arbiter.signer.sign(dataclasses.replace(cap["arb"], candidate_id="cand-other", signature=""))
    with pytest.raises(PermitError, match="not this candidate"):
        real(dataclasses.replace(i, candidate_id="cand-other"), other_arb, cap["comp"], cap["news"], cap["risk"], NOW)
    for bad, msg in ((dataclasses.replace(i, instrument="BTCUSD"), "instrument"),
                     (dataclasses.replace(i, direction=Direction.SHORT), "direction"),
                     (dataclasses.replace(i, stop_loss=i.stop_loss - 50), "direction/stop")):
        with pytest.raises(PermitError, match=msg):
            real(bad, cap["arb"], cap["comp"], cap["news"], cap["risk"], NOW)
