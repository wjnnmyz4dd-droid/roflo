"""Assembly of one system instance. Keys are created here and handed to exactly one owner each."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sentinel.arbiter import Arbiter
from sentinel.authority import DecisionSigner, PermitIssuer, PermitKey
from sentinel.broker.sim import SimBroker
from sentinel.compliance import AccountConfig, ComplianceEngine, load_profile
from sentinel.contracts import Bar
from sentinel.control import ControlState
from sentinel.execution import ExecutionService
from sentinel.finder import Finder
from sentinel.journal import Journal
from sentinel.lease import Lease
from sentinel.news import NewsEngine
from sentinel.orchestrator import Components, Orchestrator
from sentinel.reconcile import Reconciler
from sentinel.risk import RiskEngine, RiskLimits
from sentinel.validator import CostModel, Validator

VERSIONS = {"sentinel": "0.1.0", "schema": "1.0.0"}

DEFAULT_COSTS = {
    # price units; conservative retail CFD assumptions (spread, round-trip commission, per-side slippage)
    "XAUUSD": CostModel(spread=0.30, commission_price=0.10, slippage=0.10),
    "USOIL": CostModel(spread=0.04, commission_price=0.01, slippage=0.01),
    "BTCUSD": CostModel(spread=30.0, commission_price=20.0, slippage=10.0),
    "USDJPY": CostModel(spread=0.012, commission_price=0.006, slippage=0.005),
}


class ReplayMarket:
    """Serves only bars that have CLOSED as of ``now`` (the feed itself cannot leak the future)."""

    def __init__(self, bars: dict[str, list[Bar]], bar_seconds: int, source: str, retrieved_at: int, crypto=("BTCUSD",)):
        import bisect

        self._bars = bars
        self._ts = {k: [b.ts for b in v] for k, v in bars.items()}
        self._bisect = bisect.bisect_right
        self.bar_seconds = bar_seconds
        self.source = source
        self.retrieved_at = retrieved_at
        self.crypto = set(crypto)

    def bars(self, instrument: str, now: int):
        k = self._bisect(self._ts.get(instrument, []), now - self.bar_seconds)
        return self._bars.get(instrument, [])[max(0, k - 2000):k], self.bar_seconds, self.retrieved_at, self.source

    def market_open(self, instrument: str, now: int) -> bool:
        from datetime import UTC, datetime

        if instrument in self.crypto:
            return True
        d = datetime.fromtimestamp(now, UTC)
        return d.weekday() < 5 and not (d.weekday() == 4 and d.hour >= 21)


@dataclass
class Built:
    orch: Orchestrator
    comps: Components
    broker: SimBroker
    journal: Journal


def build(workdir: str, clock, market, instruments, mode="paper", profile_id="ftmo-2step-challenge-standard",
          initial_capital=100_000.0, account_start_utc=0, instance="A", news_snapshot=None, risk_limits=None,
          broker: SimBroker | None = None, independence: float | None = None, lease_ttl: float = 30.0,
          wall_clock=None, simulation: bool = True) -> Built:
    os.makedirs(workdir, exist_ok=True)
    journal = Journal(os.path.join(workdir, f"journal-{instance}.db"), clock=clock)
    broker = broker or SimBroker(os.path.join(workdir, "broker.db"), initial_balance=initial_capital, clock=clock,
                                 slippage={k: v.slippage for k, v in DEFAULT_COSTS.items()})
    lease = Lease(os.path.join(workdir, "coord.db"), owner=instance, ttl=lease_ttl, clock=clock)
    lease.acquire()
    control = ControlState(journal, clock)
    control.startup(instance, VERSIONS)
    ok, why = journal.verify()
    if not ok:  # tampered / corrupted history: nothing derived from it can be trusted
        control.halt("orchestrator", f"journal integrity failure: {why}", scope="manual")

    keys = {name: DecisionSigner(name) for name in ("arbiter", "compliance", "news", "risk")}
    permit_key = PermitKey()
    news = NewsEngine(signer=keys["news"])
    if news_snapshot is not None:
        news.load(news_snapshot)
    cfg = AccountConfig(profile_id, initial_capital, account_start_utc, simulation=simulation)
    compliance = ComplianceEngine(cfg, load_profile(profile_id), news, signer=keys["compliance"], wall_clock=wall_clock)
    risk = RiskEngine(risk_limits or RiskLimits(), initial_capital, signer=keys["risk"])
    issuer = PermitIssuer(permit_key, {k: v.key for k, v in keys.items()})
    execution = ExecutionService(broker, journal, permit_key, lease, clock, control) if mode == "paper" else None
    comps = Components(
        journal=journal, broker=broker, lease=lease, control=control,
        reconciler=Reconciler(broker, journal, control, lease=lease), compliance=compliance, news=news, risk=risk,
        finder=Finder(source=market.source), validator=Validator(DEFAULT_COSTS),
        arbiter=Arbiter(measured_independence=independence, signer=keys["arbiter"]),
        issuer=issuer, execution=execution, market=market, instruments=list(instruments),
    )
    return Built(Orchestrator(comps, mode), comps, broker, journal)
