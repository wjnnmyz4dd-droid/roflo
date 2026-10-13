"""The Final Business Rule, run through the whole pipeline rather than argued.

A human would take five hours; DeskPilot takes a fraction of a second. The
same job, the same deliverable, two contracts:

* fixed price — the full agreed price is earned, because that is what a fixed
  price is for and it is where the margin lives;
* hourly — the hours that actually elapsed, at the agreed rate, and nothing
  else, even though the human-equivalent figure is sitting right there and is
  three hundred times larger.

The second is the one worth testing hard. Nothing in the code has to be
*broken* for an hourly invoice to come out wrong — it only has to reach for
the wrong one of two numbers that are both correct answers to different
questions.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from tests_solvent.fixtures import give_an_issuer
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.errors import FailClosed
from solvent.harness import (
    OWNER, Solvent, owner_promotions, promote_capability, run_business_cycle,
)
from solvent.quoting import BillingBasis, WorkStandard
from solvent.types import Criticality, Requirement, RequirementSource

#: TEST-ONLY. The suite never asserts this is a real benchmark; what is under
#: test is that a citation is carried at all.
STANDARD = WorkStandard(
    unit="row", minutes_per_unit=0.15, setup_minutes=10.0,
    source="TEST-ONLY", citation="fixture standard, not a real benchmark",
    retrieved_date="2026-10-12")

HOURLY_RATE = 6_500   # $65/h, TEST-ONLY
QUOTED = 40_000       # $400 fixed, agreed before work began
ROWS = 2_000


def req(rid: str, check: str) -> Requirement:
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params={},
        criticality=Criticality.MANDATORY)


AGREED = [req("R1", "drop_exact_duplicates"), req("R2", "trim_whitespace")]


class Billing(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "billing.db"))
        give_an_issuer(self.s)
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the billing suite")
        self.s.discovery.register_source(
            FixtureSource("board", [{
                "ref": "bill-1", "title": "Deduplicate a customer export",
                "quoted_cents": QUOTED, "needs": ["csv-cleanup"],
                "project_state": "CT", "client_ref": "client:acme",
                "body": "clean it", "deliverables": ["clean.csv"],
                "platform_fee_cents": 4_000}]),
            owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture")
        self.s.policy.amend({
            "discovery": {"approved_sources": ["board"]},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
            # The owner's recorded cost of running DeskPilot. TEST-ONLY.
            "commercial": {"compute_cents_per_cpu_hour": 50,
                           "platform_fee_fraction": 0.10,
                           "payment_fee_fraction": 0.029,
                           "payment_fee_flat_cents": 30,
                           "billing_increment_minutes": 15},
        }, OWNER, "test: one board, recorded operating costs")

        self.source = self.dir / "customers.csv"
        rows = ["Name,Email,Amount"]
        for i in range(ROWS):
            rows.append(f"  Client {i % 800} ,c{i % 800}@x.test,{i % 97}.50")
        self.source.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def cycle(self, basis=BillingBasis.FIXED_PRICE, hourly=0, **kwargs):
        defaults = dict(
            source_name="board", source_file=str(self.source),
            workdir=self.box.name, agreed_requirements=AGREED,
            owner_accepts_quote=True, simulate_payment=True,
            billing_basis=basis, work_standard=STANDARD, work_units=ROWS,
            agreed_hourly_cents=hourly)
        return run_business_cycle(self.s, **{**defaults, **kwargs})

    def invoice(self, cycle) -> str:
        return pathlib.Path(cycle.invoice_path).read_text(encoding="utf-8")


class AFixedPriceEarnsTheWholePrice(Billing):

    def setUp(self):
        super().setUp()
        self.c = self.cycle()

    def test_the_cycle_completes(self):
        self.assertTrue(self.c.completed,
                        f"{self.c.stopped_at}: {self.c.why_stopped}")

    def test_the_full_agreed_price_is_invoiced_however_fast_the_work_was(self):
        self.assertEqual(self.c.invoiced_cents, QUOTED)
        self.assertLess(self.c.execution_wall_seconds, 60.0,
                        "this job is supposed to be fast; if it is not, the "
                        "assertion above has stopped meaning anything")

    def test_the_human_equivalent_appears_as_context_and_not_as_a_charge(self):
        text = self.invoice(self.c)
        self.assertIn("not a charge", text)
        self.assertIn("Amount due: $400.00", text)
        self.assertGreater(self.c.human_equivalent_hours, 4.0)

    def test_the_invoice_never_states_the_human_hours_as_hours_worked(self):
        text = self.invoice(self.c)
        self.assertNotIn("Worked", text,
                         "a fixed-price invoice has no hours line at all")
        self.assertNotIn(f"{self.c.human_equivalent_hours:.4f}", text)

    def test_profit_is_the_price_less_what_was_actually_spent(self):
        self.assertGreater(self.c.operating_cost_cents, 0,
                           "no operating cost was recorded, so the margin "
                           "below is measuring nothing")
        self.assertEqual(self.c.actual_profit_cents,
                         self.c.collected_cents - self.c.operating_cost_cents)

    def test_the_margin_is_not_eaten_by_a_wage_deskpilot_does_not_pay(self):
        margin = self.c.actual_profit_cents / self.c.invoiced_cents
        self.assertGreater(margin, 0.80,
                           "the margin collapsed, which is what happens when "
                           "DeskPilot costs its own work at a human wage")

    def test_the_operating_cost_is_compute_and_fees_and_says_so(self):
        joined = " ".join(self.c.operating_cost_basis)
        self.assertIn("CPU-seconds", joined)
        self.assertIn("platform fee", joined)
        self.assertIn("payment fee", joined)

    def test_the_cost_reached_the_ledger_as_real_entries(self):
        from solvent.types import CostCategory

        rows = self.s.store.raw_readonly(
            "SELECT * FROM ledger_entries WHERE job_id = ?", (self.c.job_id,))
        self.assertTrue(rows, "no cost was recorded against the job")
        categories = {r["category"] for r in rows}
        self.assertIn(CostCategory.PLATFORM_FEES.value, categories)
        for labour in (CostCategory.ON_SITE_LABOR.value,
                       CostCategory.REMOTE_LABOR.value):
            self.assertNotIn(labour, categories,
                             "a human wage was booked against an AI job")


class AnHourlyContractBillsWhatHappened(Billing):

    def setUp(self):
        super().setUp()
        self.c = self.cycle(basis=BillingBasis.HOURLY, hourly=HOURLY_RATE)

    def test_the_cycle_completes(self):
        self.assertTrue(self.c.completed,
                        f"{self.c.stopped_at}: {self.c.why_stopped}")

    def test_the_hours_billed_are_the_hours_that_elapsed(self):
        """Rounded up to the agreed increment, and no further."""
        worked = self.c.execution_wall_seconds / 3600.0
        self.assertLessEqual(self.c.billable_hours, 0.25 + 1e-9)
        self.assertGreaterEqual(self.c.billable_hours, worked)

    def test_the_human_equivalent_is_not_what_was_billed(self):
        """The whole rule, in one assertion."""
        self.assertGreater(self.c.human_equivalent_hours, 4.0)
        self.assertLess(self.c.billable_hours, 1.0)
        self.assertNotAlmostEqual(self.c.billable_hours,
                                  self.c.human_equivalent_hours, places=2)
        self.assertEqual(self.c.invoiced_cents,
                         round(self.c.billable_hours * HOURLY_RATE))

    def test_the_invoice_separates_worked_from_billed(self):
        """Printing only the billed figure and calling it worked is untrue the
        moment rounding is involved, and rounding is nearly always involved."""
        text = self.invoice(self.c)
        self.assertIn("Worked    :", text)
        self.assertIn("Billed    :", text)
        self.assertIn("rounded up to the agreed billing increment", text)

    def test_the_invoice_does_not_contain_the_human_figure_at_all(self):
        text = self.invoice(self.c)
        self.assertNotIn("5.2 hour", text)
        self.assertNotIn("not a charge", text,
                         "the fixed-price context line leaked onto an hourly "
                         "invoice, where it has no business being")

    def test_it_says_plainly_that_hourly_is_the_wrong_instrument_here(self):
        self.assertTrue(self.c.conversion_advice)
        self.assertIn("rounding rather than time", self.c.conversion_advice)

    def test_the_advice_is_advice_and_did_not_change_the_invoice(self):
        self.assertEqual(self.c.invoiced_cents,
                         round(self.c.billable_hours * HOURLY_RATE),
                         "the conversion advice re-priced a contract that was "
                         "already agreed")

    def test_an_hourly_contract_with_no_agreed_rate_is_refused(self):
        """A fresh Solvent, because a board does not hand out the same
        posting twice and a second cycle here would stop at `discover`
        for a reason that has nothing to do with billing."""
        self.s.store.close()
        Billing.setUp(self)   # the base fixture only; this class's setUp
                              # already ran a cycle and consumed the posting
        other = self.cycle(basis=BillingBasis.HOURLY, hourly=0)
        self.assertEqual(other.stopped_at, "invoice",
                         f"stopped at {other.stopped_at}: {other.why_stopped}")
        self.assertIn("rate the client agreed to", other.why_stopped)
        self.assertTrue(other.delivered,
                        "the work was done; it is the invoice that cannot be "
                        "written, and the client should not lose the file")

    def test_hourly_earns_far_less_than_fixed_for_identical_work(self):
        """Stated as a test because it is the business case for the rule.

        This is not a defect to fix by padding the clock. It is the reason to
        sell outcomes, and the system says so rather than quietly making the
        hours up.
        """
        self.assertLess(self.c.invoiced_cents, QUOTED / 10)


class TheTwoNumbersNeverCross(Billing):
    """Direct attempts to bill the value as time, through the real pipeline."""

    def test_the_billing_guard_refuses_the_human_figure(self):
        from solvent.quoting import (ExecutionMeasurement, human_equivalent,
                                     refuse_fabricated_hours)

        c = self.cycle(basis=BillingBasis.HOURLY, hourly=HOURLY_RATE)
        measurement = ExecutionMeasurement(
            wall_seconds=c.execution_wall_seconds,
            cpu_seconds=c.execution_cpu_seconds)
        human = human_equivalent(ROWS, STANDARD)
        with self.assertRaises(FailClosed) as caught:
            refuse_fabricated_hours(human.hours, measurement, human)
        self.assertIn("never billed as time", str(caught.exception))

    def test_a_fixed_price_job_records_no_billable_hours_at_all(self):
        c = self.cycle()
        self.assertEqual(c.billable_hours, 0.0,
                         "a fixed-price job computed billable hours, which is "
                         "a number with nowhere legitimate to go")

    def test_both_bases_deliver_the_same_verified_file(self):
        """The deliverable does not depend on how it is paid for."""
        fixed = self.cycle()
        self.s.store.close()
        Billing.setUp(self)
        hourly = self.cycle(basis=BillingBasis.HOURLY, hourly=HOURLY_RATE)
        self.assertEqual(
            pathlib.Path(fixed.deliverable_path).read_text(encoding="utf-8"),
            pathlib.Path(hourly.deliverable_path).read_text(encoding="utf-8"))


class NothingWaitsOnPurpose(Billing):
    """`Do not artificially delay delivery`, as a measurement."""

    def test_a_whole_cycle_finishes_in_under_a_second(self):
        c = self.cycle()
        self.assertTrue(c.completed, c.why_stopped)
        self.assertLess(c.execution_wall_seconds, 1.0,
                        f"execution took {c.execution_wall_seconds:.2f}s for "
                        f"{ROWS} rows, which suggests something is waiting")

    def test_the_pipeline_contains_no_sleep(self):
        """Read off the source, because a delay short enough to pass the timing
        assertion above is still a delay somebody put there on purpose."""
        import inspect

        from solvent import harness

        source = inspect.getsource(harness.run_business_cycle)
        source += inspect.getsource(harness._execute_job)
        for banned in ("time.sleep(", "asyncio.sleep(", "threading.Event().wait",
                       "select.select("):
            self.assertNotIn(banned, source,
                             f"the delivery path calls {banned}")


if __name__ == "__main__":
    unittest.main()
