"""Chaos / fault injection: infrastructure failures, compound worst-moment failure, AI-free decision path."""

from __future__ import annotations

import pathlib
import sqlite3

import pytest
from conftest import NOW, fixture_calendar, make_system, trending_bars

from sentinel.broker.base import BrokerError
from sentinel.contracts import Bar
from sentinel.journal import Journal
from sentinel.news import NewsEngine

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "sentinel"


def sends(b):
    return [c for c in b.broker.calls if c.startswith("send")]


def test_disk_full_before_intent_persist_means_no_order(tmp_path, clock, monkeypatch):
    b = make_system(tmp_path, clock)
    real = Journal.append

    def failing(self, type_, *a, **k):
        if type_ == "INTENT_PERSISTED":
            raise sqlite3.OperationalError("database or disk is full")
        return real(self, type_, *a, **k)

    monkeypatch.setattr(Journal, "append", failing)
    r = b.orch.cycle(NOW)
    assert r["status"] == "HALTED" and "disk is full" in r["reason"]
    assert sends(b) == [] and b.broker.open_position_count() == 0


def test_database_unavailable_everything_fails_closed(tmp_path, clock, monkeypatch):
    b = make_system(tmp_path, clock)

    def dead(self, *a, **k):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(Journal, "append", dead)
    r = b.orch.cycle(NOW)
    assert r["status"] == "HALTED" and sends(b) == []


def test_corrupted_journal_file_prevents_startup(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.journal.close()
    p = tmp_path / "journal-A.db"
    p.write_bytes(b"\x00garbage" * 1000)
    for side in ("-wal", "-shm"):
        (tmp_path / f"journal-A.db{side}").unlink(missing_ok=True)
    with pytest.raises(sqlite3.DatabaseError):
        make_system(tmp_path, clock, broker=b.broker)
    assert sends(b) == []


def test_tampered_journal_halts_at_startup(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)
    b.journal.close()
    raw = sqlite3.connect(str(tmp_path / "journal-A.db"))
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("UPDATE events SET payload='{}' WHERE seq=3")
    raw.commit()
    raw.close()
    b2 = make_system(tmp_path, clock, broker=b.broker)
    r = b2.orch.cycle(NOW)
    ok, why = b2.comps.control.trading_permitted()
    assert not ok and "integrity" in why and r["status"] in ("HALTED", "STANDBY")  # STANDBY: old lease not yet expired


def test_broker_credential_failure_halts(tmp_path, clock, monkeypatch):
    b = make_system(tmp_path, clock)

    def denied():
        raise BrokerError("AUTH_FAILED invalid credentials")

    monkeypatch.setattr(b.broker, "account", denied)
    r = b.orch.cycle(NOW)
    assert r["status"] == "HALTED" and sends(b) == []


def test_stale_market_feed_no_trades(tmp_path, clock):
    b = make_system(tmp_path, clock)
    clock.advance(6 * 3600)  # no new bars, quotes not updated
    r = b.orch.cycle(clock.now())
    assert sends(b) == [] and b.broker.open_position_count() == 0
    assert all(c.get("verdict") != "FILLED" for c in r.get("candidates", []))


@pytest.mark.parametrize("bid,ask", [(0.0, 0.3), (2000.0, 1990.0), (2000.0, 2030.0)])
def test_impossible_or_extreme_quotes_block(tmp_path, clock, bid, ask):
    b = make_system(tmp_path, clock)
    b.broker.set_quote("XAUUSD", bid, ask, NOW)
    b.orch.cycle(NOW)
    assert sends(b) == []


def test_flash_move_through_stop_fills_at_gap_and_is_journaled(tmp_path, clock):
    b = make_system(tmp_path, clock)
    b.orch.cycle(NOW)
    pos = b.broker.account().positions[0]
    clock.advance(60)
    b.broker.set_quote("XAUUSD", pos.stop_loss - 50, pos.stop_loss - 49.7, clock.now())
    b.orch.cycle(clock.now())
    closed = list(b.journal.events(type_="POSITION_CLOSED"))
    assert closed and closed[0].payload["price"] <= pos.stop_loss - 50


def test_calendar_source_outage_keeps_old_snapshot_then_fails_closed():
    n = NewsEngine()
    n.load(fixture_calendar(fetched_at=NOW))

    def outage(now):
        raise TimeoutError("calendar API timeout / rate limited (HTTP 429)")

    ok, why = n.refresh(outage, NOW + 600)
    assert not ok and n.snapshot is not None
    assert n.evaluate("XAUUSD", ("XAU", "USD"), NOW + 600).verdict.value == "ALLOW"  # still fresh enough
    assert n.evaluate("XAUUSD", ("XAU", "USD"), NOW + 7 * 3600).verdict.value == "BLOCK"  # stale -> closed


def test_worst_possible_moment_compound_failure(tmp_path, clock):
    """Open position + pending uncertain order + broker disconnect + journal tampering + stale feed + restart."""
    bars = {"XAUUSD": trending_bars("XAUUSD"),
            "BTCUSD": [Bar("BTCUSD", x.ts, x.open * 100, x.high * 100, x.low * 100, x.close * 100) for x in trending_bars("BTCUSD", seed=11)]}
    b = make_system(tmp_path, clock, bars=bars)
    b.comps.risk.limits = type(b.comps.risk.limits)(max_spread_to_stop_fraction=0.5)
    real_send = b.broker.send_order

    def send(*a, **k):  # the BTCUSD order is executed by the broker but the response is lost
        if a[1] == "BTCUSD":
            b.broker.arm_fault("send", "timeout_after_fill")
        return real_send(*a, **k)

    b.broker.send_order = send
    b.orch.cycle(NOW)
    unknown = list(b.journal.events(type_="ORDER_UNKNOWN"))
    assert len(unknown) == 1, "precondition: a pending uncertain order must exist"
    assert b.broker.open_position_count() == 2  # XAUUSD acked + BTCUSD filled-but-unacknowledged
    n_sends = len([c for c in b.broker.calls if c.startswith("send")])
    b.broker.set_connected(False)  # broker disconnect
    b.journal.close()
    raw = sqlite3.connect(str(tmp_path / "journal-A.db"))  # local DB corruption
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("UPDATE events SET payload='{\"x\":1}' WHERE seq=2")
    raw.commit()
    raw.close()
    clock.advance(3 * 3600)  # stale market feed (no new bars/quotes) and a VPS/process restart:
    b2 = make_system(tmp_path, clock, bars=bars, broker=b.broker)
    assert b2.orch.cycle(clock.now())["status"] in ("HALTED", "STANDBY")
    b.broker.set_connected(True)
    assert b2.orch.cycle(clock.now())["status"] == "HALTED"  # integrity failure is a manual halt: no improvisation
    assert b.broker.open_position_count() == 2  # nothing opened, nothing lost, nothing closed blindly
    assert len([c for c in b.broker.calls if c.startswith("send")]) == n_sends  # uncertain order never re-sent
    ok, why = b2.comps.control.trading_permitted()
    assert not ok and "integrity" in why


def test_no_llm_or_network_text_in_the_decision_path():
    banned = ("anthropic", "openai", "claude_agent_sdk", "langchain", "transformers", "subprocess", "os.system", "eval(", "exec(", "pickle")
    for p in SRC.rglob("*.py"):
        if "research" in p.parts:
            continue
        text = p.read_text()
        hits = [b for b in banned if b in text]
        assert not hits, (p.name, hits)
