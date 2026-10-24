"""The fictional customers, and the rules the simulation plays by.

Three claims, and the third is the one that makes the other two worth
anything.

**Deterministic.** Same seed, same population, same choices. A figure
that cannot be reproduced cannot be audited.

**Separated.** The customer model decides how clients behave,
DeskPilot decides what the business does about it, and
``simulation.audit`` grades. ``simulation.customers`` holds no
reference to a ``Solvent``, so a client cannot start reacting to the
system under test -- a client who adapts to it is a client who
flatters it.

**Honest about its own gaps.** Two acts have no production
counterpart and both are marked and counted. A run that quietly did
DeskPilot's work for it would measure the simulation.
"""

from __future__ import annotations

import ast
import datetime
import os
import pathlib
import secrets
import tempfile
import unittest
from collections import Counter
from unittest import mock

from solvent.runtime import Runtime
from solvent.storefront import OFFERS
from solvent.types import JobState

from simulation import rail
from simulation.calendar import Calendar
from simulation.customers import (
    Population, a_customer, nondeterminism_in_this_module, unseeded_random_in,
)
from simulation.shop import open_a_shop, what_is_blocked
from simulation.world import World

HERE = pathlib.Path(__file__).resolve().parent.parent / "simulation"
START = datetime.date(2026, 1, 5)          # a Monday


def a_population(seed: str = "test", days: int = 10, **kw) -> Population:
    pop = Population(seed=seed, length_days=days, **kw)
    pop.build(list(range(days)))
    return pop


class TheyAreTheSameEveryTime(unittest.TestCase):

    def test_the_module_draws_from_nothing_it_did_not_seed(self):
        self.assertEqual(nondeterminism_in_this_module(), set())

    def test_the_guard_catches_an_unseeded_draw(self):
        """The control. ``random.choice`` and ``Random(s).choice`` are
        the same attribute, so only the receiver tells them apart."""
        with tempfile.TemporaryDirectory() as box:
            path = pathlib.Path(box) / "probe.py"
            path.write_text("import random\nx = random.choice([1])\n",
                            encoding="utf-8")
            self.assertEqual(unseeded_random_in(path), {"random.choice"})

            path.write_text("import random\n"
                            "x = random.Random('s').choice([1])\n",
                            encoding="utf-8")
            self.assertEqual(unseeded_random_in(path), set())

    def test_two_populations_from_one_seed_are_the_same_people(self):
        first, second = a_population(), a_population()

        self.assertEqual(
            [(c.customer_id, c.company, c.budget_cents, c.wants, c.door,
              c.arrives_on) for c in first.customers],
            [(c.customer_id, c.company, c.budget_cents, c.wants, c.door,
              c.arrives_on) for c in second.customers])

    def test_a_different_seed_is_different_people(self):
        self.assertNotEqual(
            [c.customer_id for c in a_population("one").customers],
            [c.customer_id for c in a_population("two").customers])

    def test_a_stream_is_keyed_not_sequential(self):
        """So adding a customer does not shift anybody else's draws."""
        pop = a_population()
        before = pop.stream("bob", 3, "quiet").random()
        pop.stream("alice", 1, "reply").random()
        pop.stream("carol", 9, "revision").random()

        self.assertEqual(pop.stream("bob", 3, "quiet").random(), before)

    def test_an_unseeded_population_is_refused(self):
        with self.assertRaises(ValueError):
            Population(seed="", length_days=5)


class WhoTheyAre(unittest.TestCase):

    def test_every_address_is_unreachable_by_construction(self):
        for customer in a_population(days=30).customers:
            with self.subTest(customer.customer_id):
                self.assertTrue(customer.email.endswith(".invalid"),
                                customer.email)

    def test_every_company_is_distinct(self):
        """``CRM.discover`` deduplicates on (company, state): two
        clients with one name in one state become one company."""
        people = a_population(days=60, arrivals_per_day=2.0).customers
        self.assertGreater(len(people), 60, "too few to be worth checking")
        self.assertEqual(len(people), len({c.company for c in people}))

    def test_they_want_things_the_shop_actually_sells(self):
        sold = {offer.capability for offer in OFFERS}
        for customer in a_population(days=30).customers:
            with self.subTest(customer.customer_id):
                self.assertIn(customer.wants, sold)

    def test_budgets_span_both_sides_of_what_the_shop_can_do(self):
        """A population that can all afford the price measures nothing."""
        budgets = [c.budget_cents for c in a_population(days=90).customers]

        self.assertLess(min(budgets), 10_000)
        self.assertGreater(max(budgets), 60_000)

    def test_a_temperament_is_fixed_at_creation(self):
        import random as _r

        customer = a_customer(_r.Random("x"), index=1, arrives_on=0,
                              door="order")
        before = customer.temperament

        customer.note(3, "something happened")

        self.assertIs(customer.temperament, before)
        self.assertEqual(customer.history[-1]["what"], "something happened")

    def test_arrivals_land_only_on_the_days_they_were_offered(self):
        pop = Population(seed="t", length_days=10, arrivals_per_day=1.0)
        pop.build([0, 2, 4])

        self.assertEqual(sorted({c.arrives_on for c in pop.customers}),
                         [0, 2, 4])


class TheyDoNotKnowAboutDeskPilot(unittest.TestCase):
    """The separation, asserted on the import graph rather than hoped for."""

    def imports(self, name: str) -> set:
        tree = ast.parse((HERE / name).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add(node.module or "")
        return found

    def test_the_customer_model_never_imports_the_grader(self):
        for module in ("customers.py", "world.py"):
            with self.subTest(module):
                self.assertNotIn("simulation.audit", self.imports(module))

    def test_the_customer_model_holds_no_solvent(self):
        """It may read production *types*; it may not hold the system."""
        source = (HERE / "customers.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree)
                 if isinstance(node, ast.Name)}

        self.assertNotIn("solvent", names)
        self.assertNotIn("Solvent", names)

    def test_nothing_in_solvent_imports_the_simulation(self):
        root = HERE.parent / "solvent"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name.split(".")[0] == "simulation"
                           for a in node.names):
                        offenders.append(path.name)
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] == "simulation":
                        offenders.append(path.name)

        self.assertEqual(offenders, [],
                         "production imports the simulation, which would "
                         "make the measurement part of what it measures")


class WhatTheShopIsAllowedToDo(unittest.TestCase):

    def setUp(self):
        key = mock.patch.dict(os.environ,
                              {"SOLVENT_OWNER_KEY": secrets.token_hex(32)})
        key.start()
        self.addCleanup(key.stop)
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.root = pathlib.Path(self.box.name)
        self.s = open_a_shop(self.root / "shop")
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_simulation_is_on_and_may_not_be_turned_off_by_a_run(self):
        blocked = what_is_blocked(self.s)
        self.assertTrue(blocked["simulation_only"])

    def test_it_may_invoice_because_the_owner_is_recorded(self):
        allowed, why = what_is_blocked(self.s)["may_invoice"]
        self.assertTrue(allowed, why)

    def test_it_may_never_connect_a_payment_rail(self):
        """The tax reference is deliberately absent, so this stays shut."""
        allowed, why = what_is_blocked(self.s)["may_connect_a_payment_rail"]
        self.assertFalse(allowed)
        self.assertIn("tax_reference", why)

    def test_the_owner_is_the_registered_one_and_not_an_invented_one(self):
        from simulation.shop import OWNER_IDENTITY

        registered = self.s.policy.get("governance", "owner_identities",
                                       default=[])
        self.assertIn(OWNER_IDENTITY, registered)


class OneWeekOfBusiness(unittest.TestCase):
    """The whole chain, short, with everything production doing its part."""

    def setUp(self):
        key = mock.patch.dict(os.environ, {
            "SOLVENT_OWNER_KEY": secrets.token_hex(32),
            rail.SECRET_ENV: rail.TEST_SECRET})
        key.start()
        self.addCleanup(key.stop)
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.root = pathlib.Path(self.box.name)
        self.spool = self.root / "payments-inbox"
        self.s = open_a_shop(self.root / "shop", policy={
            "runtime": {"payment_spool": str(self.spool)}})
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    #: Five days, not seven. A simulated day of a busy shop costs a
    #: couple of seconds of wall clock and this file runs six of
    #: these; the chain it exercises is the same length either way,
    #: and the long runs belong in `tools/`, not in the suite.
    DAYS = 5

    def run_days(self, days: int = 0, seed: str = "week"):
        days = days or self.DAYS
        calendar = Calendar(start=START, length=days)
        clock = calendar.clock()
        runtime = Runtime(self.s, clock=clock)
        runtime.start()
        runtime.default_tasks()
        population = Population(seed=seed, length_days=days,
                                arrivals_per_day=1.0)
        population.build([d.index for d in calendar.days() if d.working])
        world = World(solvent=self.s, population=population,
                      workroot=self.root / "world", spool=self.spool)
        for _ in calendar.run(runtime, clock=clock,
                              before_day=lambda d, c: world.open_for_business(d)):
            pass
        return world

    def test_clients_arrive_order_are_delivered_to_and_pay(self):
        world = self.run_days()

        acts = Counter(a.act for a in world.log)
        self.assertGreater(acts["place an order"], 0, acts)
        states = Counter(j.state.value for j in self.s.orchestrator.jobs())
        self.assertTrue(states, "no job ever ran")
        self.assertTrue(
            states["COMPLETE"] or states["AWAITING_PAYMENT"],
            f"nothing reached delivery: {states}")

    def test_no_money_a_run_produces_is_ever_counted_as_revenue(self):
        """The one figure a simulation must never be able to move."""
        self.run_days()

        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_no_act_is_left_without_a_production_counterpart(self):
        """There were two, and there are none.

        The counter and its marker stay: the claim is worth
        re-checking on every run, because a simulation that quietly
        does DeskPilot's work for it is measuring itself.
        """
        world = self.run_days()

        marked = [a for a in world.log if a.act.startswith("[outside-world]")]
        self.assertEqual(len(marked), world.unbacked_acts)
        self.assertEqual(world.unbacked_acts, 0,
                         "; ".join(a.line() for a in marked))

    def test_the_marker_would_still_show_one(self):
        """The control. A counter that cannot count is not a counter."""
        world = self.run_days()
        customer = world.population.customers[0]

        world._the_outside_world(customer, 0, "something DeskPilot cannot do",
                                 "no production path exists for this")

        self.assertEqual(world.unbacked_acts, 1)
        self.assertTrue(world.log[-1].act.startswith("[outside-world]"))

    def test_a_client_who_sends_nothing_gets_no_work_done(self):
        """Asserted on the run, not on the code: every job that ran had
        a real source file, because the storefront refuses an order
        whose intake answers are missing."""
        self.run_days()

        for order in self.s.storefront.orders():
            with self.subTest(order.order_id):
                named = str(order.answers.get("source_file", ""))
                self.assertTrue(named and pathlib.Path(named).is_file(),
                                f"{order.order_id} names no readable file")

    def test_two_runs_of_one_seed_do_the_same_business(self):
        first = self.run_days()
        first_acts = Counter(a.act for a in first.log)
        first_states = Counter(j.state.value
                               for j in self.s.orchestrator.jobs())

        self._close()
        self.s = open_a_shop(self.root / "shop-two", policy={
            "runtime": {"payment_spool": str(self.root / "spool-two")}})
        self.spool = self.root / "spool-two"
        second = self.run_days()

        self.assertEqual(first_acts, Counter(a.act for a in second.log))
        self.assertEqual(first_states,
                         Counter(j.state.value
                                 for j in self.s.orchestrator.jobs()))

    def test_every_job_that_was_paid_for_was_delivered_first(self):
        """The order that matters: no payment is collected for work
        that never left."""
        self.run_days()

        for job in self.s.orchestrator.jobs():
            if self.s.ledger.collected_for(job.id) <= 0:
                continue
            with self.subTest(job.id):
                self.assertIn(job.state,
                              (JobState.COMPLETE, JobState.AWAITING_PAYMENT,
                               JobState.REVISION_REQUESTED))


if __name__ == "__main__":
    unittest.main()
