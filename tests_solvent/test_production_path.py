"""The scheduler-to-business-cycle connection, executed rather than described.

This module exists because of a specific, embarrassing failure. The
runtime had a ninth scheduled task named ``business``; it took a lease,
checked four refusals, and called ``Operator.run(self._one_business_job)``.
All of that worked. ``_one_business_job`` itself read
``self.solvent.policy.owner`` -- an attribute ``PolicyStore`` has never
had -- and omitted ``source_file`` and ``workdir``, which
``run_business_cycle`` declares with no defaults. Either fault raises on
the first call.

It was never caught because **every** test that exercised the business
tick replaced that method, including an acceptance run that reported a
hundred opportunities and fifty-three deliveries. Its own docstring said
so: "``_one_business_job`` is replaced with one that pulls the next
synthetic contract off a queue". The fifty-three deliveries were real
work through the real downstream pipeline, but the link from the
scheduler to that pipeline was a substitute. Certifying a function by
swapping it out certifies the swap.

So the rule for this module, and it is the whole point of it:

    **Nothing here may mock, replace, monkeypatch or subclass
    _business_tick, _one_business_job, next_work_order, Operator.run,
    run_business_cycle, qualification, the Governor, the Action Gate,
    the workers, the checkers, or the audit log.**

What *is* synthetic: the opportunities, which arrive as storefront
orders a customer could have placed, and the files they name. Nothing
reaches a marketplace, a client or a payment rail --
``egress.simulation_only`` stays on throughout and is asserted.

``TheDefectCannotComeBack`` reintroduces each original fault into a
copy of the runtime and requires this suite to notice. A suite that
still passes with the defect present is not evidence of anything.
"""

from __future__ import annotations

import os
import pathlib
import random
import secrets
import tempfile
import unittest
from unittest import mock

from solvent import certgen
from solvent import workorder
from solvent import storefront as sf
from solvent.discovery import Compliance, Readiness
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability
from solvent.runtime import Runtime
from tests_solvent.fixtures import give_an_issuer

SKILLS = ("csv-cleanup", "data-entry", "document-processing",
          "report-builder", "spreadsheet-automation")


def _case(capability: str, seed: str = "production"):
    """One generated case, from the generators the certification uses."""
    import inspect

    rng = random.Random(f"{capability}:{seed}")
    generator = certgen.GENERATORS[capability]
    if "index" in inspect.signature(generator).parameters:
        return generator(rng, 0)
    return generator(rng)


def _source_bytes(case) -> bytes:
    return case.source_bytes or (case.source_text or "").encode("utf-8")


class ProductionPath(unittest.TestCase):
    """A real Solvent, a real storefront, a real runtime. No substitutes."""

    #: Cents the owner is willing to have delivered without being asked
    #: each time. A standing grant with a ceiling, which is what makes
    #: unattended delivery legitimate rather than a bypass.
    STANDING_DELIVERY_CEILING = 500_000

    def setUp(self):
        # Per-test, never at import. The first version set this at module
        # scope with `os.environ.setdefault`, which gave every other test
        # in the run an owner key they had not asked for: 283 tests that
        # pass alone failed in the suite, because the ones asserting
        # fail-closed-without-a-key no longer had the condition they
        # were testing. A fixture that reaches outside its own test is
        # not a fixture.
        key = mock.patch.dict(os.environ,
                              {"SOLVENT_OWNER_KEY": secrets.token_hex(32)})
        key.start()
        self.addCleanup(key.stop)
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.root = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.root / "deskpilot.db"))
        self.addCleanup(self._close)
        give_an_issuer(self.s)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="production-path acceptance")
        self.s.discovery.register_source(
            self.s.storefront.source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED,
            determination="inbound orders placed against published offers")
        self.s.policy.amend(self._policy(), OWNER, "production-path acceptance")

    def _close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def _policy(self) -> dict:
        return {
            "discovery": {"approved_sources": ["storefront"]},
            "financial": {
                "max_job_spend_cents": 500_000,
                "owner_approval_above_cents": 10_000_000,
                "max_committed_unverified_cents": 10_000_000,
                "margin_floor": 0.30,
            },
            "commercial": {"minimum_project_fee_cents": 4_000},
            "operations": {
                "continuous_enabled": True,
                "max_jobs_per_run": 6,
                "max_committed_cents": 10_000_000,
                "work_root": str(self.root / "work"),
            },
            "egress": {
                "simulation_only": True,
                "allowlist": [{"host": "client.example.com",
                               "max_privacy": "CLIENT_CONFIDENTIAL",
                               "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
            },
            "permissions": {"C2_EXTERNAL_COMMUNICATION": "PREAUTHORIZED"},
            "preauthorizations": [{
                "action_class": "C2_EXTERNAL_COMMUNICATION",
                "max_cents": self.STANDING_DELIVERY_CEILING,
                "why": "owner's bounded standing grant for unattended delivery",
            }],
        }

    # ---- placing work the way a customer would -----------------------

    def place(self, capability: str, *, order_id: str = "",
              cents: int = 15_000, answers: dict | None = None) -> str:
        """One order against the published offer for this skill."""
        case = _case(capability)
        order_id = order_id or f"ord-{capability}"
        source = self.root / f"{order_id}-{case.source_name or 'source.dat'}"
        source.write_bytes(_source_bytes(case))
        service = sf.offer(capability)
        self.assertIsNotNone(service, f"no published offer for {capability}")
        full = {key: "as the offer describes" for key in service.intake_keys()}
        full["source_file"] = str(source)
        # The structured answers. A term that names `params_from` is a
        # check that both instructs and verifies, and the instruction is
        # the customer's: their column names, their calculation, their
        # section headings. Taken here from the certification generator's
        # own parameters for this case, so the fixtures are the canonical
        # ones rather than something invented for this test.
        for term in service.requirements:
            if not term.params_from:
                continue
            if term.check == "report_section":
                # The sections this customer is asking for. A list, so it
                # expands into one requirement each.
                fields = case.params.get("report_facts_rendered", {}).get(
                    "fields", [])
                column = case.params.get("report_total_correct", {}).get(
                    "column", "amount")
                full[term.params_from] = [
                    {"kind": "facts", "heading": "Overview", "fields": fields},
                    {"kind": "table", "heading": "Lines"},
                    {"kind": "total", "heading": "Totals", "column": column},
                ]
                continue
            full[term.params_from] = dict(case.params.get(term.check, {}))
        full.update(answers or {})
        order = self.s.storefront.place(
            order_id=order_id, offer_id=capability,
            client_ref=f"client:{capability}", answers=full,
            offered_cents=cents)
        self.assertIs(order.state, sf.OrderState.RECEIVED,
                      f"{order.state}: {order.why} {order.missing}")
        return order_id

    def runtime(self) -> Runtime:
        """Exactly what ``serve()`` builds, minus the real-time sleep."""
        runtime = Runtime(self.s)
        runtime.start()
        runtime.default_tasks()
        return runtime

    def tick(self, runtime: Runtime) -> str:
        """Run the scheduled business task. Not a stand-in for it."""
        task = [t for t in runtime.tasks if t.name == "business"]
        self.assertEqual(len(task), 1, "there must be exactly one business task")
        return task[0].run()


class TheRealCycleRuns(ProductionPath):
    """A to D of the acceptance gate: scheduler, Operator, cycle, arguments."""

    def test_an_order_goes_all_the_way_through_the_scheduled_task(self):
        self.place("csv-cleanup")
        runtime = self.runtime()
        line = self.tick(runtime)

        summary = runtime._business
        self.assertEqual(summary["attempted"], 1, line)
        self.assertGreater(summary["committed_cents"], 0,
                           f"nothing was invoiced: {line}")

        jobs = self.s.orchestrator.jobs()
        self.assertEqual(len(jobs), 1, "exactly one job should exist")
        self.assertIn(jobs[0].state.value,
                      ("AWAITING_PAYMENT", "PAID", "COMPLETE"),
                      f"job stopped at {jobs[0].state.value}")

    def test_the_cycle_really_ran_and_left_durable_evidence(self):
        """Not "a function returned" -- rows in the database."""
        self.place("csv-cleanup")
        self.tick(self.runtime())

        events = [e["event"] for e in self.s.audit.events()]
        for required in ("runtime.business_ran",):
            self.assertIn(required, events)
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)

        job = self.s.orchestrator.jobs()[0]
        self.assertTrue(self.s.governor.decisions_for(job.id),
                        "the Governor recorded no decision")
        self.assertTrue(self.s.ledger.invoices_for(job.id)
                        if hasattr(self.s.ledger, "invoices_for") else True)

    def test_the_owner_identity_comes_from_the_policy(self):
        """Not a constant, and not invented."""
        resolved = workorder.owner_identity_of(self.s.policy)
        self.assertTrue(self.s.policy.is_owner(resolved),
                        f"{resolved!r} is not a registered owner")

    def test_the_work_order_matches_the_cycle_signature(self):
        """Every key it passes is a parameter the function declares."""
        import inspect

        from solvent.harness import run_business_cycle

        self.place("csv-cleanup")
        order = workorder.next_work_order(
            self.s, workroot=str(self.root / "work"))
        self.assertIsNotNone(order)
        declared = set(inspect.signature(run_business_cycle).parameters)
        for key in order.as_cycle_kwargs():
            self.assertIn(key, declared,
                          f"{key!r} is not a parameter of run_business_cycle")
        required = {name for name, p in
                    inspect.signature(run_business_cycle).parameters.items()
                    if p.default is inspect.Parameter.empty and name != "s"}
        self.assertTrue(required <= set(order.as_cycle_kwargs()),
                        f"missing required: {required - set(order.as_cycle_kwargs())}")


class AllFiveSkillsGoThrough(ProductionPath):
    """E of the gate. One real order per skill, through the real path."""

    def test_every_skill_executes_through_the_scheduler(self):
        for capability in SKILLS:
            self.place(capability, order_id=f"ord-{capability}")

        runtime = self.runtime()
        # More than one tick: `max_jobs_per_run` bounds each run, and the
        # point is that successive scheduled runs keep going.
        for _ in range(5):
            self.tick(runtime)

        by_capability = {}
        for job in self.s.orchestrator.jobs():
            by_capability.setdefault(job.capability if hasattr(job, "capability")
                                     else "", []).append(job.state.value)
        states = [j.state.value for j in self.s.orchestrator.jobs()]
        self.assertEqual(len(states), len(SKILLS),
                         f"expected one job per skill, got {states}")
        delivered = [s for s in states
                     if s in ("AWAITING_PAYMENT", "PAID", "COMPLETE")]
        self.assertEqual(len(delivered), len(SKILLS),
                         f"not every skill reached delivery: {states}")

    def test_no_order_is_executed_twice(self):
        self.place("csv-cleanup")
        runtime = self.runtime()
        for _ in range(3):
            self.tick(runtime)
        self.assertEqual(len(self.s.orchestrator.jobs()), 1,
                         "the same order produced more than one job")


class NoWorkIsARealAnswer(ProductionPath):
    """H of the gate."""

    def test_an_empty_shop_stops_with_nothing_to_do(self):
        runtime = self.runtime()
        line = self.tick(runtime)
        self.assertIn("no eligible opportunity", line)
        self.assertEqual(self.s.orchestrator.jobs(), [])

    def test_next_work_order_returns_none_rather_than_a_stand_in(self):
        self.assertIsNone(workorder.next_work_order(
            self.s, workroot=str(self.root / "work")))

    def test_nothing_is_invented_to_look_productive(self):
        runtime = self.runtime()
        for _ in range(3):
            self.tick(runtime)
        self.assertEqual(self.s.orchestrator.jobs(), [])
        self.assertEqual(self.s.ledger.real_revenue_cents()
                         if hasattr(self.s.ledger, "real_revenue_cents") else 0, 0)


class SimulationHolds(ProductionPath):
    """J of the gate."""

    def test_simulation_is_still_on_after_a_run(self):
        self.place("csv-cleanup")
        self.tick(self.runtime())
        self.assertTrue(self.s.policy.get("egress", "simulation_only",
                                          default=True))

    def test_no_real_revenue_was_recorded(self):
        self.place("csv-cleanup")
        self.tick(self.runtime())
        if hasattr(self.s.ledger, "real_revenue_cents"):
            self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_the_loop_refuses_when_simulation_is_turned_off(self):
        self.s.policy.amend({"egress": {"simulation_only": False}}, OWNER,
                            "test: the owner turned simulation off")
        self.place("csv-cleanup")
        line = self.tick(self.runtime())
        self.assertIn("simulation", line.lower())
        self.assertEqual(self.s.orchestrator.jobs(), [])


class SingleExecutorHolds(ProductionPath):
    """The lease, with two runtimes against one database."""

    def test_a_second_runtime_cannot_run_the_business_at_the_same_time(self):
        self.place("csv-cleanup")
        first, second = Runtime(self.s), Runtime(self.s)
        first.start(); first.default_tasks()
        second.start(); second.default_tasks()
        self.assertNotEqual(first._lease_id, second._lease_id)

        held = first._take_business_lease()
        self.assertIsNotNone(held)
        try:
            line = self.tick(second)
            self.assertIn("lease", line.lower())
            self.assertEqual(self.s.orchestrator.jobs(), [],
                             "the second executor ran a job anyway")
        finally:
            first._release_business_lease(held)

    def test_the_lease_is_released_so_the_next_tick_can_run(self):
        self.place("csv-cleanup")
        runtime = self.runtime()
        self.tick(runtime)
        self.assertIsNotNone(runtime._take_business_lease(),
                             "the lease was not released after the run")


class RestartPreservesState(ProductionPath):
    """I of the gate. A new Runtime over the same database."""

    def test_a_delivered_job_is_still_delivered_after_a_restart(self):
        self.place("csv-cleanup")
        self.tick(self.runtime())
        before = [(j.id, j.state.value) for j in self.s.orchestrator.jobs()]
        self.assertTrue(before)

        restarted = Runtime(self.s)
        restarted.start()
        restarted.default_tasks()
        after = [(j.id, j.state.value) for j in self.s.orchestrator.jobs()]
        self.assertEqual(before, after, "the restart changed settled work")
        ok, detail = self.s.audit.verify_chain()
        self.assertTrue(ok, detail)

    def test_an_order_already_in_the_pipeline_is_not_run_again(self):
        self.place("csv-cleanup")
        self.tick(self.runtime())
        count = len(self.s.orchestrator.jobs())
        restarted = Runtime(self.s)
        restarted.start(); restarted.default_tasks()
        self.tick(restarted)
        self.assertEqual(len(self.s.orchestrator.jobs()), count,
                         "the restart re-ran an order that was already done")


class RefusalsAreReal(ProductionPath):
    """G of the gate, and the adversarial list."""

    def test_an_order_naming_a_file_that_is_not_there_is_refused(self):
        self.place("csv-cleanup", answers={"source_file": "/nowhere/absent.csv"})
        runtime = self.runtime()
        line = self.tick(runtime)
        self.assertIn("no eligible opportunity", line)
        self.assertEqual(self.s.orchestrator.jobs(), [])
        reasons = [e["why"] for e in self.s.audit.events()
                   if e.get("event") == "operation.order_not_executable"]
        self.assertTrue(any("is not there" in r for r in reasons), reasons)

    def test_an_unstructured_answer_is_refused_rather_than_interpreted(self):
        self.place("data-entry", answers={"field_mapping": "just map it sensibly"})
        self.tick(self.runtime())
        self.assertEqual(self.s.orchestrator.jobs(), [])
        reasons = [e["why"] for e in self.s.audit.events()
                   if e.get("event") == "operation.order_not_executable"]
        self.assertTrue(any("structured answer" in r for r in reasons), reasons)

    def test_no_registered_owner_means_nothing_runs(self):
        with self.assertRaises(FailClosed):
            workorder.owner_identity_of(_PolicyWithNoOwner())

    def test_the_owner_identity_is_never_defaulted(self):
        """A constant would be a forged identity."""
        import inspect

        source = inspect.getsource(workorder.owner_identity_of)
        self.assertNotIn('"owner:root"', source)
        self.assertNotIn("return OWNER", source)

    def test_the_halt_stops_new_work(self):
        from solvent.types import OperatingMode

        self.place("csv-cleanup")
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER,
                                         "test: the owner pulled the halt")
        self.tick(self.runtime())
        self.assertEqual(self.s.orchestrator.jobs(), [],
                         "a job started while halted")

    def test_an_unprofitable_order_is_not_delivered(self):
        """The margin floor binds on the production path.

        Raised rather than lowering the price: at the shop's own minimum
        the job is genuinely profitable, so a cheap order proves nothing.
        What has to hold is that the floor the owner set is the floor the
        autonomous loop honours.
        """
        self.s.policy.amend({"financial": {"margin_floor": 0.99}}, OWNER,
                            "test: a floor nothing clears")
        self.place("csv-cleanup")
        self.tick(self.runtime())
        states = [j.state.value for j in self.s.orchestrator.jobs()]
        self.assertNotIn("AWAITING_PAYMENT", states,
                         f"work below the margin floor was delivered: {states}")

    def test_without_a_standing_grant_delivery_needs_a_signed_approval(self):
        """The loop never mints its own authority to deliver.

        Withdrawing the grant, rather than lowering its ceiling: the
        ceiling bounds the *spend* an action may make, and a delivery
        spends nothing, so a low ceiling does not test this. What does
        is removing the owner's standing permission altogether, after
        which the Action Gate wants a signed approval the loop has no
        way to produce.
        """
        self.s.policy.amend({"preauthorizations": []}, OWNER,
                            "test: the owner withdrew the standing grant")
        self.place("csv-cleanup")
        self.tick(self.runtime())
        states = [j.state.value for j in self.s.orchestrator.jobs()]
        self.assertNotIn("AWAITING_PAYMENT", states,
                         f"delivered with no owner authority at all: {states}")
        self.assertTrue(states, "the job should exist, held before delivery")

    def test_an_offer_promising_an_unregistered_check_cannot_be_sold(self):
        bad = sf.ServiceOffer(
            offer_id="bogus", capability="csv-cleanup", title="t", summary="s",
            scope_included=(), scope_excluded=(), deliverables=(),
            intake=(), minimum_cents=1, turnaround="soon",
            requirements=(sf.OfferTerm(check="make_it_nice",
                                       acceptance="nice"),))
        self.assertIn("does not register", sf.offer_problem(bad))

    def test_an_offer_promising_nothing_cannot_be_sold(self):
        bare = sf.ServiceOffer(
            offer_id="bare", capability="csv-cleanup", title="t", summary="s",
            scope_included=(), scope_excluded=(), deliverables=(),
            intake=(), minimum_cents=1, turnaround="soon")
        self.assertIn("nothing that can be checked", sf.offer_problem(bare))


class _PolicyWithNoOwner:
    """A policy whose owner list is empty. Not a mock of anything under test."""

    def get(self, *path, default=None):
        return [] if path[:2] == ("governance", "owner_identities") else default


class TheDefectCannotComeBack(ProductionPath):
    """K of the gate.

    Each original fault is reintroduced and the acceptance must notice.
    A suite that still passes with the defect present proves nothing, so
    these tests assert that the *certifying* assertion fails.

    Deliberately the one place in this module that touches the methods
    under test: it exists to break them. Every other class runs them as
    they ship.
    """

    def _delivers(self, runtime) -> bool:
        """The assertion the acceptance makes, as a boolean."""
        try:
            self.tick(runtime)
        except Exception:
            return False
        return any(j.state.value in ("AWAITING_PAYMENT", "PAID", "COMPLETE")
                   for j in self.s.orchestrator.jobs())

    def test_the_real_path_does_deliver(self):
        """The control. Without this the three below prove nothing."""
        self.place("csv-cleanup")
        self.assertTrue(self._delivers(self.runtime()),
                        "the unmodified path must deliver, or the defect "
                        "injections below are meaningless")

    def test_the_original_owner_attribute_defect_is_caught(self):
        self.place("csv-cleanup")
        runtime = self.runtime()
        from solvent.harness import run_business_cycle

        def broken():
            # Exactly what shipped: PolicyStore has no `owner`.
            return run_business_cycle(
                self.s, source_name="",
                owner_identity=self.s.policy.owner)

        runtime._one_business_job = broken
        self.assertFalse(self._delivers(runtime),
                         "the suite passed with the original defect present")

    def test_omitting_source_file_and_workdir_is_caught(self):
        self.place("csv-cleanup")
        runtime = self.runtime()
        from solvent.harness import run_business_cycle

        def broken():
            return run_business_cycle(self.s, source_name="storefront",
                                      owner_identity=OWNER)

        runtime._one_business_job = broken
        self.assertFalse(self._delivers(runtime))

    def test_a_disconnected_business_job_is_caught(self):
        self.place("csv-cleanup")
        runtime = self.runtime()
        runtime._one_business_job = lambda: None
        self.assertFalse(self._delivers(runtime),
                         "a no-op business job passed the acceptance")

    def test_a_tick_that_does_nothing_is_caught(self):
        """Replaced before the task is registered, which is the only way.

        The first attempt set the attribute after ``default_tasks()`` and
        the acceptance still passed -- because ``Task`` holds the bound
        method it was given, so the replacement was never called. Worth
        keeping: it is the same shape of mistake as certifying a method
        by substituting it, and here it made a defect-injection test
        inject nothing.
        """
        self.place("csv-cleanup")
        runtime = Runtime(self.s)
        runtime._tick_business = lambda: "not run: disconnected"
        runtime.start()
        runtime.default_tasks()
        self.assertFalse(self._delivers(runtime))

    def test_removing_the_business_task_is_caught(self):
        self.place("csv-cleanup")
        runtime = Runtime(self.s)
        runtime.start()
        runtime.default_tasks()
        runtime.tasks = [t for t in runtime.tasks if t.name != "business"]
        with self.assertRaises(AssertionError):
            self.tick(runtime)
