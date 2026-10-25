"""A grader that cannot fail is not a grader.

The first class is the separation: it may read DeskPilot and it may
read what the simulation wrote down, and it may not share code with
either. The last class is the control -- each reconciliation is shown
failing on a corrupted input, because a report of twelve green ticks
means nothing unless a tick can go red.
"""

from __future__ import annotations

import ast
import datetime
import json
import os
import pathlib
import secrets
import tempfile
import unittest
from unittest import mock

from simulation import audit, books, rail
from simulation.calendar import Calendar
from simulation.customers import Population
from simulation.shop import open_a_shop
from simulation.world import World
from solvent.runtime import Runtime

HERE = pathlib.Path(__file__).resolve().parent.parent / "simulation"
START = datetime.date(2026, 1, 5)


class ItSharesNoCodeWithEitherSide(unittest.TestCase):

    def imports(self) -> set:
        tree = ast.parse((HERE / "audit.py").read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add(node.module or "")
        return found

    def test_it_does_not_import_the_world_it_is_grading(self):
        for forbidden in ("simulation.world", "simulation.customers",
                          "simulation.market", "simulation.books"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, self.imports())

    def test_it_keeps_its_own_list_of_the_ledgers_lines(self):
        """Imported from books.py it would be checking a claim against
        the module making the claim."""
        self.assertNotIn("simulation.books", self.imports())
        self.assertEqual(len(audit.LEDGER_LINES), 5)

    def test_that_list_is_still_what_production_books(self):
        from solvent.quoting import OperatingCost

        lines = OperatingCost(compute_cents=1, api_cents=1,
                              platform_fee_cents=1, payment_fee_cents=1,
                              verification_cents=1).as_cost_lines()

        self.assertEqual({line.category.value for line in lines},
                         set(audit.LEDGER_LINES))


class AShortRunReconciles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._key = mock.patch.dict(os.environ, {
            "SOLVENT_OWNER_KEY": secrets.token_hex(32),
            rail.SECRET_ENV: rail.TEST_SECRET})
        cls._key.start()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.spool = cls.root / "payments-inbox"
        cls.journal_path = cls.root / "books.jsonl"
        cls.s = open_a_shop(cls.root / "shop",
                            policy={"runtime": {"payment_spool": str(cls.spool)}})
        journal = books.Journal.for_shop(cls.s, cls.journal_path)
        calendar = Calendar(start=START, length=5)
        clock = calendar.clock()
        runtime = Runtime(cls.s, clock=clock)
        runtime.start()
        runtime.default_tasks()
        population = Population(seed="grading", length_days=5,
                                arrivals_per_day=2.0)
        population.build([d.index for d in calendar.days() if d.working])
        cls.world = World(solvent=cls.s, population=population,
                          workroot=cls.root / "world", spool=cls.spool)
        for day in calendar.run(runtime, clock=clock,
                                before_day=lambda d, c:
                                cls.world.open_for_business(d)):
            books.post_the_days_costs(journal, cls.s, day.index)
        journal.close()
        cls.acts = [a.__dict__ for a in cls.world.log]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.s.store.close()
        except Exception:
            pass
        cls._tmp.cleanup()
        cls._key.stop()

    def grade(self, **over):
        kwargs = {"acts": self.acts, "journal_path": self.journal_path,
                  "expect_paid": False}
        kwargs.update(over)
        return audit.reconcile(self.s, **kwargs)

    def test_the_two_sets_of_records_agree(self):
        grade = self.grade()

        self.assertTrue(grade.passed, grade.report())

    def test_it_checked_more_than_a_couple_of_things(self):
        self.assertGreaterEqual(len(self.grade().findings), 10)

    def test_it_read_both_sides(self):
        grade = self.grade()

        self.assertGreater(grade.figures["deskpilot"]["audit_rows"], 0)
        self.assertGreater(grade.figures["simulation"]["acts"], 0)

    def test_the_report_names_every_reconciliation(self):
        text = self.grade().report()

        self.assertIn("PASS", text)
        self.assertIn("the audit chain verifies", text)

    def test_the_profit_and_loss_adds_up(self):
        money = audit.profit_and_loss(self.s, journal_path=self.journal_path)

        self.assertEqual(money["total_costs_cents"],
                         money["direct_costs_cents"] + money["overhead_cents"])
        self.assertEqual(
            money["accrual_profit_cents"],
            money["invoiced_cents"] - money["total_costs_cents"])
        self.assertEqual(
            money["cash_profit_cents"],
            money["cash_in_cents"] - money["total_costs_cents"])
        self.assertEqual(money["cash_in_cents"],
                         money["collected_cents"] + money["deposits_cents"])

    def test_every_money_line_says_where_it_came_from(self):
        money = audit.profit_and_loss(self.s, journal_path=self.journal_path)

        for line in ("invoiced", "collected", "deposits", "direct_costs",
                     "overhead"):
            with self.subTest(line):
                self.assertTrue(money["basis"][line])

    def test_the_overhead_is_labelled_as_an_accrual(self):
        money = audit.profit_and_loss(self.s, journal_path=self.journal_path)

        self.assertIn("not money production spent", money["basis"]["overhead"])

    def test_no_simulated_money_reads_as_revenue(self):
        money = audit.profit_and_loss(self.s, journal_path=self.journal_path)

        self.assertEqual(money["real_revenue_cents"], 0)


class EachCheckCanActuallyFail(AShortRunReconciles):
    """The control. A corrupted input per reconciliation."""

    def test_a_client_the_simulation_never_sent_is_caught(self):
        thinned = [dict(a, customer_id="somebody-else") for a in self.acts]

        grade = self.grade(acts=thinned)

        self.assertFalse(grade.passed)
        self.assertTrue(any("every client DeskPilot worked for" in f.what
                            for f in grade.failures), grade.report())

    def test_a_missing_signature_is_caught(self):
        without = [a for a in self.acts if a.get("act") != "sign"]

        grade = self.grade(acts=without)

        self.assertTrue(any("signature" in f.what for f in grade.failures),
                        grade.report())

    def test_an_act_with_no_production_path_is_caught(self):
        marked = self.acts + [{"day": 0, "customer_id": "x", "done": True,
                               "act": "[outside-world] something",
                               "detail": "no production path"}]

        grade = self.grade(acts=marked)

        self.assertTrue(any("no path for" in f.what
                            for f in grade.failures), grade.report())

    def test_a_truncated_journal_row_is_caught(self):
        broken = self.root / "broken.jsonl"
        broken.write_text(self.journal_path.read_text(encoding="utf-8")
                          + '{"day": 1, "line": "ACQ', encoding="utf-8")

        grade = self.grade(journal_path=broken)

        self.assertTrue(any("lost to a crash" in f.what
                            for f in grade.failures), grade.report())

    def test_a_journal_booking_a_ledger_line_is_caught(self):
        """The double count the whole arrangement exists to prevent."""
        doubled = self.root / "doubled.jsonl"
        doubled.write_text(
            self.journal_path.read_text(encoding="utf-8")
            + json.dumps({"day": 1, "line": "COMPUTE", "cents": 500,
                          "why": "the same machine, paid for twice",
                          "ref": ""}) + "\n",
            encoding="utf-8")

        grade = self.grade(journal_path=doubled)

        self.assertFalse(grade.passed)
        self.assertTrue(any("already booked" in f.what
                            for f in grade.failures), grade.report())

    def test_expecting_payment_in_a_run_where_nobody_paid_is_caught(self):
        grade = self.grade(expect_paid=True)

        self.assertFalse(grade.passed)
        self.assertTrue(any("somebody paid something" in f.what
                            for f in grade.failures), grade.report())

    def test_a_failing_grade_says_so_in_one_word(self):
        self.assertIn("FAIL", self.grade(expect_paid=True).report())


if __name__ == "__main__":
    unittest.main()
