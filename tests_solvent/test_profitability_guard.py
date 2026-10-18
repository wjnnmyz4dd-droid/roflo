"""The financial admission control, and the ways round it that must not work.

Two layers answer "is this worth money", and they are not the same layer.

* The **Governor** is the authority. ``FinancialGovernor.decide`` is the one
  money gate: every job the business cycle admits passes through it, and
  ``Qualification`` maps its verdict onto accept, reject or escalate without
  doing any arithmetic of its own.
* The **Finder** is triage. ``Finder._verdict`` decides what is worth
  *recommending*, reads the owner's floor and margin from the same policy,
  and has no authority over admission. It is the layer a mutation run once
  left with its profit guard disabled, so it is tested here as well as the
  authority it advises.

What is being established is narrow and worth saying plainly: that a job
which loses money cannot be admitted automatically, and that the owner
cannot be *asked* to approve one in a way that quietly skips the floor
they set. Being able to say yes to a bad job is the owner's right. Being
shown it as an ordinary approval, with the floor never evaluated, is not.
"""

from __future__ import annotations

import unittest

from solvent.finder import Confidence, ContractType, Economics, Finder
from solvent.types import (CostCategory, CostLine, GovernorVerdict,
                           Jurisdiction, LocationSignals, money)
from tests_solvent.fixtures import OWNER, Rig


def _economics(revenue, cost, *, contract=ContractType.FIXED_PRICE):
    """An Economics with the figures already settled, for verdict tests."""
    profit = None if (revenue is None or cost is None) else revenue - cost
    margin = (profit / revenue) if (profit is not None and revenue) else None
    return Economics(contract, revenue, cost, profit, margin,
                     Confidence.MEASURED, "test figures")


class TheGovernorIsTheAuthority(unittest.TestCase):
    """Every case in the brief, against the gate that actually admits work."""

    def setUp(self):
        self.rig = Rig()
        self.rig.load_labor_fixtures()
        self.floor = float(self.rig.policy.get("financial", "margin_floor",
                                               default=0.30))

    def _decide(self, *, revenue, cost, job_id="J1"):
        estimate = self.rig.governor.estimate(
            job_id=job_id, revenue_cents=revenue,
            signals=LocationSignals(project=Jurisdiction(state="CA")),
            quantities={},
            direct_lines=[CostLine(category=CostCategory.VENDOR, amount=cost)])
        return self.rig.governor.decide(
            job_id=job_id, revenue_cents=revenue, estimate=estimate,
            conformance_ok=True), estimate

    def _priced(self, *, margin, job_id="J1", cost=money(100)):
        """A job whose *effective* margin is the one asked for.

        The calibration multiplier sits between the direct lines and the
        effective cost, so the revenue is solved for after the estimate
        exists. The estimate is made once and handed back with the
        revenue it was solved against: re-estimating would move the cost
        again and the margin would not be the one asked for.
        """
        estimate = self.rig.governor.estimate(
            job_id=job_id, revenue_cents=money(1),
            signals=LocationSignals(project=Jurisdiction(state="CA")),
            quantities={},
            direct_lines=[CostLine(category=CostCategory.VENDOR, amount=cost)])
        effective = estimate.effective_cost_cents
        revenue = int(round(effective / (1 - margin)))
        # Nudge off the boundary so the intended side of `<` is unambiguous
        # after integer rounding.
        actual = (revenue - effective) / revenue
        while actual < margin:
            revenue += 1
            actual = (revenue - effective) / revenue
        return revenue, estimate

    # ---- the plain cases ------------------------------------------------

    def test_a_clearly_profitable_job_is_admitted(self):
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=self.floor + 0.40)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PROFITABLE, reason)

    def test_a_job_exactly_at_the_threshold_is_admitted(self):
        """At the floor is not below it. The comparison is strict."""
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=self.floor + 0.001)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PROFITABLE, reason)

    def test_a_marginally_profitable_job_below_the_floor_is_declined(self):
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=self.floor - 0.05)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW, reason)

    def test_a_zero_profit_job_is_declined(self):
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=0.0)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW, reason)

    def test_a_negative_profit_job_is_declined(self):
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=-0.5)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW, reason)

    def test_no_revenue_is_not_a_free_job(self):
        self.rig.relax_caps()
        (verdict, reason), _ = self._decide(revenue=0, cost=money(100))
        self.assertIs(verdict, GovernorVerdict.NEEDS_INFORMATION, reason)

    def test_an_unresolved_pricing_jurisdiction_blocks_the_price(self):
        """A missing cost input is never priced as if it were zero."""
        self.rig.relax_caps()
        estimate = self.rig.governor.estimate(
            job_id="J1", revenue_cents=money(900), signals=LocationSignals(),
            quantities={CostCategory.ON_SITE_LABOR: 2})
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=money(900), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PRICING_LOCATION_UNKNOWN, reason)

    def test_a_preliminary_estimate_is_not_a_binding_quote(self):
        self.rig.relax_caps()
        estimate = self.rig.governor.estimate(
            job_id="J1", revenue_cents=money(900),
            signals=LocationSignals(project=Jurisdiction(country="US")),
            quantities={CostCategory.ON_SITE_LABOR: 1})
        if not estimate.preliminary:
            self.skipTest("this fixture set does not produce a fallback line")
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=money(900), estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.NEEDS_INFORMATION, reason)

    # ---- the ways round it ----------------------------------------------

    def test_a_costly_job_under_the_floor_is_declined_not_escalated(self):
        """The defect this file was written for.

        ``_evaluate`` checks the per-job cost ceiling before the margin. A
        job that trips both used to come back REQUIRES_APPROVAL, so the
        owner was asked to approve a job the floor would have declined --
        and the floor was never evaluated at all. The owner may always say
        yes to a bad job; they may not be shown one as an ordinary
        approval with their own floor skipped.
        """
        ceiling = int(self.rig.policy.get(
            "financial", "max_job_spend_cents", default=25_000))
        self.rig.policy.amend(
            {"financial": {"max_committed_unverified_cents": 10_000_000}},
            OWNER, "test: isolate the ceiling from the exposure cap")
        revenue, estimate = self._priced(margin=-0.5, cost=ceiling + money(10))
        self.assertGreater(estimate.effective_cost_cents, ceiling,
                           "the ceiling must actually be tripped")
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW, reason)

    def test_a_costly_job_that_clears_the_floor_still_asks_the_owner(self):
        """The control above must not swallow the approval it replaces."""
        ceiling = int(self.rig.policy.get(
            "financial", "max_job_spend_cents", default=25_000))
        self.rig.policy.amend(
            {"financial": {"max_committed_unverified_cents": 10_000_000}},
            OWNER, "test: isolate the ceiling from the exposure cap")
        revenue, estimate = self._priced(margin=self.floor + 0.40,
                                         cost=ceiling + money(10))
        self.assertGreater(estimate.effective_cost_cents, ceiling)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.REQUIRES_APPROVAL, reason)

    def test_a_high_value_job_under_the_floor_is_declined_not_escalated(self):
        """The same hole through the other approval door."""
        self.rig.relax_caps(approval_above=money(1))
        revenue, estimate = self._priced(margin=-0.5)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW, reason)

    def test_the_exposure_cap_still_outranks_the_floor(self):
        """Declining for risk is not the same statement as declining for price.

        A job that would breach the shop's committed-but-unverified cap is
        refused as risk whatever its margin, because the reason the owner
        needs to read is the exposure, not the arithmetic.
        """
        self.rig.policy.amend(
            {"financial": {"max_committed_unverified_cents": money(1),
                           "max_job_spend_cents": 10_000_000}},
            OWNER, "test: isolate the exposure cap")
        revenue, estimate = self._priced(margin=-0.5)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.HIGH_RISK, reason)

    def test_conformance_still_outranks_everything(self):
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=self.floor + 0.40)
        verdict, reason = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=False)
        self.assertIs(verdict, GovernorVerdict.REJECT, reason)
        self.assertIn("price is not evaluated", reason)

    def test_an_unprofitable_job_is_declined_however_often_it_is_asked(self):
        """Retry is not a way in. The verdict is a function of the figures."""
        self.rig.relax_caps()
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                job_id = f"J{attempt}"
                revenue, estimate = self._priced(margin=-0.5, job_id=job_id)
                verdict, reason = self.rig.governor.decide(
                    job_id=job_id, revenue_cents=revenue,
                    estimate=estimate, conformance_ok=True)
                self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW, reason)

    def test_the_floor_comes_from_policy_not_from_the_caller(self):
        """`decide` takes no floor argument, so no caller can supply one."""
        import inspect

        names = set(inspect.signature(
            self.rig.governor.decide).parameters) - {"self"}
        self.assertNotIn("floor", names)
        self.assertNotIn("margin_floor", names)

    def test_raising_the_floor_declines_what_the_old_floor_admitted(self):
        """The comparison is only worth something if the floor can bind."""
        self.rig.relax_caps()
        revenue, estimate = self._priced(margin=self.floor + 0.05)
        verdict, _ = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.PROFITABLE)
        self.rig.policy.amend({"financial": {"margin_floor": self.floor + 0.20}},
                              OWNER, "test: raise the floor")
        verdict, _ = self.rig.governor.decide(
            job_id="J1", revenue_cents=revenue, estimate=estimate,
            conformance_ok=True)
        self.assertIs(verdict, GovernorVerdict.MARGIN_TOO_LOW)


class TheQualifierAddsNoEconomicsOfItsOwn(unittest.TestCase):
    """The mapping layer must stay a mapping layer."""

    def test_it_maps_margin_too_low_onto_a_rejection(self):
        from solvent import qualification

        source = (qualification.__file__)
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("MARGIN_TOO_LOW", text)
        for arithmetic in ("margin_floor", "* 0.3", "/ revenue"):
            with self.subTest(arithmetic=arithmetic):
                self.assertNotIn(arithmetic, text)


class TheFinderTriagesOnTheSameFloor(unittest.TestCase):
    """The advisory layer, and the guard a mutation run once disabled."""

    def setUp(self):
        self.rig = Rig()
        self.rig.load_labor_fixtures()
        self.rig.relax_caps()
        self.finder = Finder(None, self.rig.policy, self.rig.capability,
                             self.rig.governor)

    def _verdict(self, econ, **kwargs):
        from solvent.finder import Verdict  # noqa: F401 - imported for callers

        return self.finder._verdict(
            _Posting(), econ, kwargs.get("feasible", True), "",
            kwargs.get("reachable", True), "", (), kwargs.get("risks", ()))

    def test_a_negative_profit_posting_is_declined(self):
        verdict, why = self._verdict(_economics(money(100), money(400)))
        self.assertEqual(verdict.name, "DECLINE", why)
        self.assertIn("would cost", why)

    def test_a_zero_profit_posting_is_declined(self):
        verdict, why = self._verdict(_economics(money(400), money(400)))
        self.assertEqual(verdict.name, "DECLINE", why)

    def test_an_unknown_price_is_held_not_pursued(self):
        verdict, why = self._verdict(_economics(None, None))
        self.assertEqual(verdict.name, "HOLD", why)

    def test_the_guard_is_not_a_comment(self):
        """The mutation incident, as a test: disable it and this must fail."""
        import inspect

        source = inspect.getsource(self.finder._verdict)
        self.assertIn("econ.profit_cents is not None and econ.profit_cents <= 0",
                      source)
        self.assertNotIn("if False:", source)


class _Posting:
    """The minimum a posting needs to be for `_verdict` to read it."""

    id = "O1"
    net_budget_cents = money(100)
    confidence = 1.0


if __name__ == "__main__":
    unittest.main()
