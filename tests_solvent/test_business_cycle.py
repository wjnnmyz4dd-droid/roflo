"""The whole freelance transaction, in simulation, with its refusals.

`run_business_cycle` is composition: discovery, qualification, the Governor,
the Orchestrator, the service pipeline, the Action Gate and the Ledger, in the
order a real job happens in. Before it existed the front half and the back half
had never met, and three defects lived exactly in the gap between them -- a
`need` meaning two different things, a pipeline walking a job to ACCEPTED
itself, and a requirement that made every discovered job impossible to commit.

So most of this file is not "the happy path works". It is the set of things
that must still refuse once the happy path works, because a pipeline that
completes is only worth having if the things that should stop it still do:

* Solvent never answers a question put to its owner.
* Work never starts without a Governor budget grant.
* A deliverable is recomputed before it leaves, and a wrong one does not.
* Simulated money is never revenue, and a job is not complete until the
  Ledger says money arrived.

Every value here is TEST-ONLY. Nothing reaches a network, a client or a rail.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.errors import FailClosed
from solvent.harness import (
    CYCLE_STAGES, FIXTURE_RATES, OWNER, Solvent, owner_promotions,
    promote_capability, run_business_cycle,
)
from solvent.types import (
    CostCategory, Criticality, Jurisdiction, JobState, PricingTier, Requirement,
    RequirementSource,
)

CSV_POSTING = {
    "ref": "cycle-csv", "title": "Deduplicate a 2,000-row customer export",
    "quoted_cents": 40_000, "needs": ["csv-cleanup"],
    "body": "Customer export has duplicate rows. Please clean and return.",
    "client_ref": "client:acme", "deliverables": ["clean.csv"],
    "platform_fee_cents": 4_000, "project_state": "CT",
}

SOURCE_CSV = ("Name,Email,Amount\n"
              "  Acme Ltd ,a@acme.test,100.00\n"
              "Acme Ltd,a@acme.test,100.00\n"
              "Beta Co,b@beta.test,250.50\n")


def req(rid: str, check: str, params: dict | None = None,
        criticality: Criticality = Criticality.MANDATORY) -> Requirement:
    """One agreed term, as a platform's structured fields would supply it."""
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params=params or {},
        criticality=criticality)


AGREED = [req("R1", "drop_exact_duplicates"),
          req("R2", "trim_whitespace"),
          req("R3", "parses_as_csv")]


class Cycle(unittest.TestCase):
    """One configured Solvent with one posting on one approved board."""

    posting = CSV_POSTING
    capability = "csv-cleanup"

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "cycle.db"))
        self.addCleanup(self.close)
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        self.source = self.dir / "customers.csv"
        self.source.write_text(SOURCE_CSV, encoding="utf-8")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def clear_for_client_work(self):
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the cycle suite")

    def load_pricing(self):
        for state, rate in FIXTURE_RATES.items():
            self.s.pricing.ingest(
                category=CostCategory.ON_SITE_LABOR,
                jurisdiction=Jurisdiction(state=state),
                tier=PricingTier.MARKET_DATA, unit_cents=rate, unit="hour",
                source="FIXTURE", effective_date="2026-06-01",
                retrieved_date="2026-09-01", confidence=0.9,
                owner_identity=OWNER, is_fixture=True)

    def register_board(self, postings=None):
        self.s.discovery.register_source(
            FixtureSource("board", list(postings or [self.posting])),
            owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture board")
        self.s.policy.amend({
            "discovery": {"approved_sources": ["board"]},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
            # This suite is about owner decisions, so the owner has to actually
            # be asked. The threshold is set on *revenue* rather than left to
            # the cost ceiling: once DeskPilot's cost model stopped charging
            # itself a human wage, a $400 job cost $0.60 and sailed under every
            # spend ceiling, which silently turned every approval test in this
            # file into a test of nothing. An owner who wants to see jobs of
            # this size says so by revenue, and that is what this sets.
            "financial": {"owner_approval_above_cents": 20_000},
        }, OWNER, "test: one board, one destination, owner sees jobs over $200")

    def run(self, **kwargs):
        defaults = dict(source_name="board", source_file=str(self.source),
                        workdir=self.box.name, capability=self.capability,
                        agreed_requirements=AGREED, owner_accepts_quote=True,
                        simulate_payment=True)
        return run_business_cycle(self.s, **{**defaults, **kwargs})

    # `run` is unittest's own method name; keep the harness one separate.
    runCycle = run

    def run(self, result=None):  # noqa: F811  -- restore TestCase.run
        return unittest.TestCase.run(self, result)


class TheWholeTransactionCompletes(Cycle):

    def setUp(self):
        super().setUp()
        self.cycle = self.runCycle()

    def test_every_stage_ran_in_order(self):
        self.assertEqual(self.cycle.stages, list(CYCLE_STAGES),
                         f"stopped at {self.cycle.stopped_at}: "
                         f"{self.cycle.why_stopped}")
        self.assertTrue(self.cycle.completed)

    def test_the_job_is_complete_and_the_audit_chain_verifies(self):
        self.assertEqual(self.cycle.final_state, JobState.COMPLETE.value)
        self.assertTrue(self.cycle.audit_chain_ok)

    def test_the_price_came_from_the_governor_not_the_headline(self):
        """The posting advertised $400. What was authorised is a cost the
        Governor computed; taking the headline as the price is the mistake this
        asserts against."""
        self.assertGreater(self.cycle.authorised_cents, 0)
        self.assertNotEqual(self.cycle.authorised_cents,
                            self.cycle.quoted_cents)
        rows = self.s.governor.decisions_for(self.cycle.job_id)
        self.assertTrue(rows, "the Governor recorded no decision")
        self.assertEqual(int(rows[-1]["effective_cost_cents"]),
                         self.cycle.authorised_cents)

    def test_work_ran_under_a_budget_grant(self):
        self.assertTrue(self.cycle.grant_id)
        grants = self.s.store.raw_readonly(
            "SELECT * FROM budget_grants WHERE job_id = ?", (self.cycle.job_id,))
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["id"], self.cycle.grant_id)

    def test_the_deliverable_was_verified_before_it_left(self):
        self.assertGreaterEqual(self.cycle.verification_rounds, 1)
        ok, why = self.s.orchestrator.verification_satisfied(self.cycle.job_id)
        self.assertTrue(ok, why)

    def test_the_deliverable_is_the_work_the_client_asked_for(self):
        """Not "a file exists". The duplicate is gone and the padding is."""
        delivered = pathlib.Path(self.cycle.deliverable_path).read_text(
            encoding="utf-8")
        self.assertEqual(delivered.strip().count("\n"), 2,
                         f"expected a header and two rows, got:\n{delivered}")
        self.assertIn("Acme Ltd,a@acme.test", delivered)
        self.assertNotIn("  Acme Ltd ", delivered)

    def test_delivery_went_through_the_action_gate_as_simulated(self):
        rows = self.s.store.raw_readonly(
            "SELECT * FROM action_requests WHERE job_id = ?",
            (self.cycle.job_id,))
        self.assertTrue(rows, "delivery did not reach the Action Gate")
        self.assertTrue(all(r["simulated"] for r in rows),
                        "an external effect was not recorded as simulated")

    def test_the_invoice_is_the_agreed_price(self):
        self.assertEqual(self.cycle.invoiced_cents, CSV_POSTING["quoted_cents"])
        self.assertEqual(self.cycle.collected_cents, self.cycle.invoiced_cents)

    def test_profit_is_collected_revenue_less_recorded_cost(self):
        book = self.s.ledger.actual_profit(self.cycle.job_id)
        self.assertEqual(book["collected_revenue_cents"],
                         self.cycle.collected_cents)
        self.assertEqual(self.cycle.actual_profit_cents,
                         book["collected_revenue_cents"]
                         - book["actual_cost_cents"])


class SimulatedMoneyIsNeverRevenue(Cycle):
    """The one result that must not improve when the pipeline starts working."""

    def setUp(self):
        super().setUp()
        self.cycle = self.runCycle()

    def test_the_ledger_reports_no_real_revenue(self):
        self.assertEqual(self.cycle.real_revenue_cents, 0)
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_the_payment_is_marked_simulated(self):
        self.assertTrue(self.cycle.payment_simulated)
        self.assertTrue(self.s.ledger.is_simulated(self.cycle.job_id))

    def test_the_business_metrics_show_no_earnings(self):
        metrics = self.s.metrics()
        self.assertEqual(metrics.real_revenue_collected, "$0.00")
        self.assertNotEqual(
            metrics.simulated_revenue_collected, "$0.00",
            "the money is not being broken out as simulated at all, so this "
            "assertion would pass on a Solvent that collected nothing")

    def test_a_completed_job_did_not_make_the_dashboard_claim_income(self):
        """A full cycle moves `jobs_completed`. It must not move earnings."""
        metrics = self.s.metrics()
        self.assertEqual(metrics.jobs_completed, 1)
        self.assertEqual(metrics.real_revenue_collected, "$0.00")
        self.assertEqual(metrics.revenue_collected,
                         metrics.simulated_revenue_collected,
                         "collected revenue that is not accounted for as "
                         "simulated is being presented as real")


class SolventNeverAnswersItsOwnersQuestion(Cycle):

    def test_without_the_owners_answer_the_job_waits(self):
        cycle = self.runCycle(owner_accepts_quote=False)
        self.assertEqual(cycle.stopped_at, "owner-approval")
        self.assertTrue(cycle.owner_approval_required)
        self.assertFalse(cycle.owner_approval_given)
        self.assertEqual(cycle.final_state,
                         JobState.AWAITING_OWNER_APPROVAL.value)

    def test_a_waiting_job_produced_nothing(self):
        cycle = self.runCycle(owner_accepts_quote=False)
        self.assertFalse(cycle.delivered)
        self.assertEqual(cycle.invoiced_cents, 0)
        self.assertEqual(
            self.s.store.raw_readonly("SELECT * FROM budget_grants"), [],
            "a job waiting on its owner was given a budget")
        self.assertEqual(
            self.s.store.raw_readonly("SELECT * FROM action_requests"), [],
            "a job waiting on its owner reached the Action Gate")

    def test_a_component_cannot_supply_the_answer(self):
        with self.assertRaises(FailClosed) as caught:
            self.runCycle(owner_identity="qualification")
        self.assertIn("registered owner", str(caught.exception))

    def test_the_service_pipeline_cannot_step_over_a_waiting_job(self):
        """The regression guard for the defect this suite was written on.

        `_execute_job` walked every job through QUALIFYING -> ACCEPTED ->
        EXECUTING, skipping only the state it was already in. From
        AWAITING_OWNER_APPROVAL that walk is the pipeline answering, under the
        initiator "execution", a question that was put to a person.
        """
        from solvent.harness import _execute_job

        cycle = self.runCycle(owner_accepts_quote=False)
        self.assertEqual(cycle.final_state,
                         JobState.AWAITING_OWNER_APPROVAL.value)
        with self.assertRaises(FailClosed) as caught:
            _execute_job(self.s, job_id=cycle.job_id, source=str(self.source),
                         workdir=self.box.name, capability=self.capability)
        message = str(caught.exception)
        self.assertIn("waiting for an owner decision", message)
        self.assertIn("owner_approves", message)
        self.assertEqual(self.s.orchestrator.job(cycle.job_id).state,
                         JobState.AWAITING_OWNER_APPROVAL)


class WorkDoesNotStartWithoutMoneyAuthorised(Cycle):

    def test_begin_execution_refuses_an_empty_grant(self):
        cycle = self.runCycle(owner_accepts_quote=False)
        self.s.orchestrator.owner_approves(
            job_id=cycle.job_id, owner_identity=OWNER, why="test")
        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.begin_execution(
                job_id=cycle.job_id, grant_id="", initiator="test")
        self.assertIn("budget grant", str(caught.exception))

    def test_the_grant_is_tied_to_the_governors_own_decision(self):
        cycle = self.runCycle()
        grant = self.s.store.raw_readonly(
            "SELECT * FROM budget_grants WHERE id = ?", (cycle.grant_id,))[0]
        decisions = {r["id"] for r in
                     self.s.governor.decisions_for(cycle.job_id)}
        self.assertIn(grant["decision_id"], decisions,
                      "a budget was granted against no recorded decision")


class ThereIsNoContractWithoutAgreedTerms(Cycle):

    def test_without_a_checklist_the_cycle_stops_at_the_contract(self):
        cycle = self.runCycle(agreed_requirements=None)
        self.assertEqual(cycle.stopped_at, "contract")
        self.assertIn("name a service, not terms", cycle.why_stopped)

    def test_nothing_was_executed_or_delivered(self):
        cycle = self.runCycle(agreed_requirements=None)
        self.assertFalse(cycle.delivered)
        self.assertEqual(cycle.committed_requirements, 0)
        self.assertEqual(self.s.orchestrator.artifacts(cycle.job_id,
                                                       role="DELIVERABLE"), [])

    def test_a_term_naming_no_registered_check_is_refused(self):
        cycle = self.runCycle(agreed_requirements=[
            req("R1", "drop_exact_duplicates"),
            req("R9", "make_it_nice")])
        self.assertEqual(cycle.stopped_at, "contract")
        self.assertIn("no registered check", cycle.why_stopped)

    def test_the_postings_own_need_does_not_block_the_checklist(self):
        """The defect: Qualification recorded "csv-cleanup" as a MANDATORY
        requirement naming no check, so `commit_requirements` refused every
        checklist that came after it -- for ever, on every discovered job."""
        cycle = self.runCycle()
        self.assertEqual(cycle.stopped_at, "")
        baseline = self.s.orchestrator.effective_baseline(cycle.job_id)
        scope = [r for r in baseline if getattr(r, "text", "") == "csv-cleanup"]
        for requirement in scope:
            self.assertIsNot(requirement.criticality, Criticality.MANDATORY,
                             "a service name is recorded as a blocking term")


class ADefectiveDeliverableDoesNotLeave(Cycle):

    def test_a_sabotaged_artifact_is_caught_and_not_delivered(self):
        """The negative control for the whole suite.

        Everything else here passes if the pipeline runs. This passes only if
        the verifier is still looking: the same cycle, with one row quietly
        added to the deliverable after the worker wrote it.
        """
        from solvent.harness import _execute_job

        cycle = self.runCycle(owner_accepts_quote=False)
        self.s.orchestrator.owner_approves(job_id=cycle.job_id,
                                           owner_identity=OWNER, why="test")
        rows = self.s.governor.decisions_for(cycle.job_id)
        grant = self.s.governor.grant_budget(
            job_id=cycle.job_id, decision_id=rows[-1]["id"],
            authorized_cents=int(rows[-1]["effective_cost_cents"]))
        self.s.orchestrator.begin_execution(job_id=cycle.job_id,
                                            grant_id=grant, initiator="test")
        for requirement in AGREED:
            self.s.orchestrator.add_requirement(cycle.job_id, requirement)
        from solvent.checks import checks_for
        from solvent.harness import CAPABILITIES
        spec = CAPABILITIES[self.capability]
        for derived in spec.derive(str(self.source), list(AGREED)):
            self.s.orchestrator.add_requirement(cycle.job_id, derived)
        self.s.orchestrator.commit_requirements(
            job_id=cycle.job_id, initiator=OWNER,
            known_checks=checks_for(self.capability))
        self.s.orchestrator.register_artifact(
            job_id=cycle.job_id, role="SOURCE", path=str(self.source),
            initiator="test")

        def smuggle(path, attempt):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("Gamma Inc,g@gamma.test,999.00\n")

        report = _execute_job(self.s, job_id=cycle.job_id,
                              source=str(self.source), workdir=self.box.name,
                              capability=self.capability, sabotage=smuggle)
        self.assertFalse(report.delivered,
                         "a row the source never had reached the client")
        self.assertFalse(report.rounds[-1].passed)
        self.assertEqual(
            self.s.store.raw_readonly(
                "SELECT * FROM action_requests WHERE allowed = 1"), [],
            "the Action Gate allowed a delivery that failed verification")


class PaymentIsNotAssumed(Cycle):

    def test_without_a_payment_the_job_stays_awaiting_payment(self):
        cycle = self.runCycle(simulate_payment=False)
        self.assertEqual(cycle.stopped_at, "payment")
        self.assertTrue(cycle.delivered)
        self.assertEqual(cycle.final_state, JobState.AWAITING_PAYMENT.value)
        self.assertEqual(cycle.collected_cents, 0)

    def test_an_invoice_was_still_raised(self):
        cycle = self.runCycle(simulate_payment=False)
        self.assertIn("invoice", cycle.stages)
        payment = self.s.ledger.payment_for(cycle.job_id)
        self.assertIsNotNone(payment)
        self.assertEqual(payment["amount_cents"], CSV_POSTING["quoted_cents"])
        self.assertEqual(payment["collected_cents"], 0)

    def test_completion_is_refused_while_nothing_has_arrived(self):
        cycle = self.runCycle(simulate_payment=False)
        with self.assertRaises(FailClosed) as caught:
            self.s.orchestrator.complete(job_id=cycle.job_id, initiator="test")
        self.assertIn("no externally verified collected revenue",
                      str(caught.exception))

    def test_collection_without_a_verification_method_is_refused(self):
        cycle = self.runCycle(simulate_payment=False)
        payment = self.s.ledger.payment_for(cycle.job_id)
        from solvent.types import PaymentState
        with self.assertRaises(FailClosed) as caught:
            self.s.ledger.set_payment_state(
                payment["id"], PaymentState.PAID,
                collected_cents=CSV_POSTING["quoted_cents"])
        self.assertIn("verification method", str(caught.exception))


class WhatTheCycleRefusesToTake(Cycle):

    def test_an_opportunity_needing_something_nobody_has_is_rejected(self):
        self.s.store.close()
        self.s = Solvent(str(self.dir / "gap.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board([{**CSV_POSTING, "ref": "cycle-gap",
                              "needs": ["video_editing"]}])
        cycle = self.runCycle()
        self.assertEqual(cycle.stopped_at, "qualify")
        self.assertIn("video_editing", cycle.why_stopped)
        self.assertIn("no registered capability covers", cycle.why_stopped)

    def test_a_capability_that_is_certified_but_not_cleared_is_not_delivered(self):
        """Certification is not permission. The cycle still refuses at the
        gate even though every check passes."""
        self.s.store.close()
        self.s = Solvent(str(self.dir / "limited.db"))
        from solvent.harness import (certifiable_scope, ensure_verifier_certified,
                                     provision_development_only)
        ensure_verifier_certified(self.s, "csv-cleanup")
        provision_development_only(
            self.s, "csv-cleanup", certifiable_scope("csv-cleanup"),
            owner_identity=OWNER, why="test: build and test only")
        self.load_pricing()
        self.register_board()
        cycle = self.runCycle()
        self.assertFalse(cycle.delivered)
        self.assertIn(cycle.stopped_at, {"qualify", "deliver"},
                      f"stopped somewhere unexpected: {cycle.stopped_at} "
                      f"({cycle.why_stopped})")


class ConfirmingARequirementDoesNotRewriteIt(unittest.TestCase):
    """`confirm` said who vouched for a requirement, and quietly dropped the
    requirement. Pinned here because the business cycle is what exposed it and
    what would break again."""

    def test_the_check_acceptance_params_and_criticality_all_survive(self):
        from solvent.content import confirm

        original = Requirement(
            id="R1", text="drop duplicate rows",
            source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED,
            acceptance="no two rows identical", check="drop_exact_duplicates",
            params={"subset": ["Email"]}, criticality=Criticality.ADVISORY)
        confirmed = confirm(original, by="owner:demo",
                            source=RequirementSource.OWNER_CONFIRMED)

        self.assertEqual(confirmed.source, RequirementSource.OWNER_CONFIRMED)
        self.assertEqual(confirmed.confirmed_by, "owner:demo")
        self.assertEqual(confirmed.id, original.id)
        self.assertEqual(confirmed.text, original.text)
        self.assertEqual(confirmed.acceptance, original.acceptance)
        self.assertEqual(confirmed.check, original.check)
        self.assertEqual(confirmed.params, original.params)
        self.assertIs(confirmed.criticality, Criticality.ADVISORY)

    def test_it_still_refuses_to_confirm_to_an_unconfirmed_source(self):
        from solvent.content import confirm

        with self.assertRaises(FailClosed):
            confirm(Requirement(id="R1", text="x",
                                source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED),
                    by="owner:demo",
                    source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED)

    def test_owner_confirmation_still_requires_an_owner_identity(self):
        from solvent.content import confirm

        with self.assertRaises(FailClosed):
            confirm(Requirement(id="R1", text="x",
                                source=RequirementSource.MODEL_EXTRACTED_UNCONFIRMED),
                    by="qualification",
                    source=RequirementSource.OWNER_CONFIRMED)


if __name__ == "__main__":
    unittest.main()


class EveryClearedSkillEarnsThroughTheSameCycle(Cycle):
    """Four more opportunities, one per capability, through one function.

    The point is not that each worker works -- the capability suites establish
    that against generated evidence. It is that the *business* path is one
    path: whatever the skill, the same Governor prices it, the same owner
    approves it, the same grant funds it, the same verifier recomputes it and
    the same Ledger refuses to call simulated money revenue. A capability that
    needed its own business logic would be a second pipeline, and the second
    pipeline is always the one that skips a control.
    """

    def board(self, ref, needs, quoted_cents=40_000):
        return {**CSV_POSTING, "ref": ref, "needs": [needs],
                "quoted_cents": quoted_cents,
                "title": f"{needs} work for a client"}

    def cycle_for(self, capability, source_path, requirements, quoted=40_000):
        self.s.store.close()
        self.s = Solvent(str(self.dir / f"{capability}.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board([self.board(f"cycle-{capability}", capability,
                                        quoted)])
        return run_business_cycle(
            self.s, source_name="board", source_file=str(source_path),
            workdir=self.box.name, capability=capability,
            agreed_requirements=requirements, owner_accepts_quote=True,
            simulate_payment=True)

    def assert_earned(self, cycle, capability):
        self.assertEqual(cycle.stages, list(CYCLE_STAGES),
                         f"{capability} stopped at {cycle.stopped_at}: "
                         f"{cycle.why_stopped}")
        self.assertTrue(cycle.delivered)
        self.assertEqual(cycle.final_state, JobState.COMPLETE.value)
        self.assertEqual(cycle.collected_cents, cycle.invoiced_cents)
        self.assertTrue(cycle.audit_chain_ok)
        self.assertEqual(cycle.real_revenue_cents, 0,
                         f"{capability} booked simulated money as revenue")

    def test_a_report_builder_job_earns(self):
        import json

        facts = self.dir / "facts.json"
        facts.write_text(json.dumps({
            "client": "Acme Ltd", "period": "Q1 2026",
            "rows": [{"item": "Consulting", "amount": "100.00"},
                     {"item": "Support", "amount": "350.50"}]}),
            encoding="utf-8")
        cycle = self.cycle_for("report-builder", facts, [
            req("R1", "report_section", {"kind": "facts", "heading": "Overview",
                                         "fields": ["client", "period"]}),
            req("R2", "report_section", {"kind": "table", "heading": "Lines",
                                         "columns": ["item", "amount"]}),
            req("R3", "report_section", {"kind": "total", "heading": "Totals",
                                         "column": "amount"})])
        self.assert_earned(cycle, "report-builder")

        delivered = pathlib.Path(cycle.deliverable_path).read_text(
            encoding="utf-8")
        self.assertIn("450.50", delivered,
                      "the total the client was invoiced against is missing "
                      "or wrong")

    def test_a_spreadsheet_automation_job_earns(self):
        from solvent import xlsx

        book = self.dir / "client.xlsx"
        book.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Data",
            rows=[["Item", "Client ref", "Units", "Rate"],
                  ["  Support ", "CR-2001", 4, "25.50"],
                  ["Consulting", "CR-2002", 2, "100.00"]])]))
        cycle = self.cycle_for("spreadsheet-automation", book, [
            req("R1", "require_columns", {"columns": ["Item", "Units", "Rate"]}),
            req("R2", "trim_whitespace", {"columns": ["Item"]}),
            req("R3", "compute_column", {"into": "Amount",
                                         "expression": "product",
                                         "using": ["Units", "Rate"]}),
            req("R4", "total_row", {"aggregates": {"Amount": "sum"}})])
        self.assert_earned(cycle, "spreadsheet-automation")

        delivered = xlsx.read_workbook(
            pathlib.Path(cycle.deliverable_path).read_bytes())
        values = [[cell.value for cell in row] for row in delivered.first.rows]
        self.assertEqual(values[-1][-1], 302.0,
                         "the client was invoiced for a total that is wrong")

    def test_a_data_entry_job_earns_and_holds_its_exceptions(self):
        records = self.dir / "records.csv"
        records.write_text(
            "Ref,Client Name,Invoiced,Due,Status\n"
            "A-1001,  Acme   Corp ,1250.00,2026-03-01,open\n"
            "A-1002,Borealis GmbH,480.50,2026-03-15,paid\n"
            "A-1003,,92.00,2026-04-02,open\n", encoding="utf-8")
        cycle = self.cycle_for("data-entry", records, [
            req("R1", "map_fields", {"mapping": {
                "Reference": "Ref", "Client": "Client Name",
                "Amount": "Invoiced", "Due Date": "Due", "State": "Status"}}),
            req("R2", "normalise_whitespace", {"fields": ["Client"]}),
            req("R3", "require_field", {"fields": ["Reference", "Client"]}),
            req("R4", "records_reconcile"),
            req("R5", "exceptions_justified")])
        self.assert_earned(cycle, "data-entry")

        delivered = pathlib.Path(cycle.deliverable_path)
        text = delivered.read_text(encoding="utf-8")
        self.assertIn("Acme Corp", text)
        self.assertNotIn("A-1003", text,
                         "a record with no client was delivered anyway")
        exceptions = delivered.with_name(delivered.stem + "-exceptions.csv")
        self.assertTrue(exceptions.exists(),
                        "the client was billed without being told what was held")

    def test_a_document_processing_job_earns(self):
        from solvent import docx

        invoice = self.dir / "invoice.docx"
        invoice.write_bytes(docx.write_document([
            docx.Out(text="Invoice", style="Title"),
            docx.Out(text="Invoice Number: A-1001"),
            docx.Out(text="Client: Acme Corp"),
            docx.Out(kind="table", rows=(("Item", "Amount"),
                                         ("Consulting", "1,000.00")))]))
        cycle = self.cycle_for("document-processing", invoice, [
            req("R1", "extract_fields", {"labels": {
                "Invoice Number": ["Invoice Number"],
                "Client": ["Client"],
                "Purchase Order": ["Purchase Order"]}}),
            req("R2", "extract_table", {"columns": ["Item", "Amount"]}),
            req("R3", "fields_traceable"),
            req("R4", "absences_disclosed"),
            req("R5", "no_invented_content")])
        self.assert_earned(cycle, "document-processing")

        import json
        payload = json.loads(
            pathlib.Path(cycle.deliverable_path).read_text(encoding="utf-8"))
        values = {f["field"]: f["value"] for f in payload["fields"]}
        self.assertEqual(values["Invoice Number"], "A-1001")
        self.assertEqual(values["Purchase Order"], "NOT FOUND IN SOURCE",
                         "a field the document does not contain was invented")


class TheOwnersCommandIsTheEntryPoint(Cycle):
    """The CLI, not the function. A pipeline the owner cannot run is not one.

    These go through `cli.main` with real arguments and a real database file,
    because the thing that ships is the command, and a composition that only
    its own unit tests can reach has never been run the way it will be used.
    """

    def setUp(self):
        super().setUp()
        from solvent.discovery import ManualSource

        # The owner's own door, which is the one that survives a restart: a
        # fixture board is rebuilt empty in a new process, so it is a test
        # construct rather than a channel.
        self.s.discovery.register_source(
            ManualSource("owner_entered", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED,
            determination="owner-entered: no platform terms, no external call")
        self.s.policy.amend(
            {"discovery": {"approved_sources": ["owner_entered"]}},
            OWNER, "test: the owner's own door")
        self.db = str(self.dir / "cycle.db")
        self.s.store.close()

        import json
        self.posting_file = self.dir / "posting.json"
        self.posting_file.write_text(json.dumps({
            "ref": "cli-cycle", "title": "Deduplicate a customer export",
            "quoted_cents": 40_000, "needs": ["csv-cleanup"],
            "project_state": "CT", "client_ref": "client:acme",
            "body": "Please clean this file.", "deliverables": ["clean.csv"],
            "platform_fee_cents": 4_000}), encoding="utf-8")
        self.brief_file = self.dir / "brief.json"
        self.brief_file.write_text(json.dumps([
            {"id": "R1", "check": "drop_exact_duplicates"},
            {"id": "R2", "check": "trim_whitespace"},
            {"id": "R3", "check": "parses_as_csv"}]), encoding="utf-8")

    def invoke(self, *extra):
        import io
        from contextlib import redirect_stdout
        from solvent import cli

        argv = ["cycle", "--db", self.db, "--source", "owner_entered",
                "--posting", str(self.posting_file), "--file",
                str(self.source), "--brief", str(self.brief_file),
                "--workdir", self.box.name, *extra]
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(argv)
        return code, buffer.getvalue()

    def reopen(self):
        self.s = Solvent(self.db)
        return self.s

    def test_the_unflagged_command_stops_at_the_owners_decision(self):
        code, out = self.invoke()
        self.assertEqual(code, 1, out)
        self.assertIn("[STOPPED] owner-approval", out)
        self.assertIn("NOT GIVEN", out)
        self.assertNotIn("[done]    deliver", out)

    def test_with_the_owners_approval_it_runs_to_the_end(self):
        code, out = self.invoke("--accept-quote", "--simulate-payment")
        self.assertEqual(code, 0, out)
        for stage in CYCLE_STAGES:
            self.assertIn(f"[done]    {stage}", out, f"{stage} did not run")
        self.assertIn("final state : COMPLETE", out)
        self.assertIn("audit chain : verifies", out)

    def test_the_command_never_reports_simulated_money_as_earnings(self):
        code, out = self.invoke("--accept-quote", "--simulate-payment")
        self.assertEqual(code, 0, out)
        self.assertIn("(SIMULATED)", out)
        self.assertIn("real revenue: $0.00", out)
        self.assertEqual(self.reopen().ledger.real_revenue_cents(), 0)

    def test_a_brief_whose_term_names_no_check_is_refused_before_anything_runs(self):
        import json

        self.brief_file.write_text(json.dumps([{"id": "R1", "text": "be nice"}]),
                                   encoding="utf-8")
        code, out = self.invoke("--accept-quote")
        self.assertEqual(code, 1)
        self.assertIn("names no check", out)
        solvent = self.reopen()
        self.assertEqual(solvent.store.raw_readonly("SELECT * FROM jobs"), [],
                         "a refused brief still created a job")

    def test_an_empty_brief_is_refused(self):
        self.brief_file.write_text("[]", encoding="utf-8")
        code, out = self.invoke("--accept-quote")
        self.assertEqual(code, 1)
        self.assertIn("non-empty JSON list", out)

    def test_the_work_survives_the_process_that_did_it(self):
        """The cycle writes to a real database, not to an in-memory one."""
        self.invoke("--accept-quote", "--simulate-payment")
        solvent = self.reopen()
        jobs = solvent.store.raw_readonly("SELECT * FROM jobs")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], JobState.COMPLETE.value)
        self.assertTrue(solvent.audit.verify_chain()[0])


class KLeanTheJournalDoesNotKeepTheClientsSecrets(Cycle):
    """Advisory. Keys on what a secret *looks like*, not what a field is called.

    A posting is written by whoever placed it. Its title and its `needs` travel
    into the reasons an owner approval and a planning refusal are recorded
    under, and `audit.record` wrote them down verbatim -- while
    `record_verification` beside it had redacted its raw output since the day a
    verifier was caught quoting a client's text into the same database. Same
    exposure, same table, different treatment.

    The deliverable is still left alone, for the same reason as before:
    redacting a client's own file would be altering their data without
    authorisation.
    """

    def planted(self) -> str:
        # Assembled at run time: a literal here trips the repository's own
        # credential scanner, which is the scanner working.
        return "_".join(["sk", "live", "51H8" + "A" * 20 + "QsT"])

    def test_a_credential_in_a_posting_does_not_reach_the_audit_journal(self):
        secret = self.planted()
        self.s.store.close()
        self.s = Solvent(str(self.dir / "planted.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board([{**CSV_POSTING, "ref": "cycle-planted",
                              "title": f"Clean this export {secret}"}])
        cycle = self.runCycle()
        self.assertTrue(cycle.completed,
                        f"stopped at {cycle.stopped_at}: {cycle.why_stopped}")

        journal = repr([dict(row) for row in self.s.store.raw_readonly(
            "SELECT * FROM audit_log")])
        self.assertNotIn(secret, journal,
                         "a credential inside a client's posting was written "
                         "to the audit journal verbatim")

    def test_a_credential_in_the_job_reference_does_not_reach_the_reasons(self):
        """A second path, because the first proved only one field.

        The title travels in the audit `payload`; the opportunity's reference
        travels in `why`, which is where the owner's acceptance of the quote
        is recorded. Redacting one and not the other leaves the journal
        holding the secret just the same, and a single test over the whole
        table cannot tell which field saved it.
        """
        secret = self.planted()
        self.s.store.close()
        self.s = Solvent(str(self.dir / "planted-ref.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board([{**CSV_POSTING, "ref": f"job-{secret}"}])
        cycle = self.runCycle()
        self.assertTrue(cycle.completed,
                        f"stopped at {cycle.stopped_at}: {cycle.why_stopped}")

        reasons = [str(row["why"]) for row in self.s.store.raw_readonly(
            "SELECT why FROM audit_log")]
        self.assertTrue(any("owner accepted the quote" in r for r in reasons),
                        "the reason this test is about was never recorded, so "
                        "it would pass on a Solvent that recorded nothing")
        for reason in reasons:
            self.assertNotIn(secret, reason,
                             "a credential inside a client's job reference "
                             "was written into an audit reason")

    def test_the_owners_own_reason_is_not_mangled(self):
        """The negative control. A redactor that ate ordinary prose would make
        the journal useless, and this would pass on one that redacted
        everything."""
        cycle = self.runCycle()
        reasons = [str(row["why"]) for row in self.s.store.raw_readonly(
            "SELECT why FROM audit_log WHERE job_id = ?", (cycle.job_id,))]
        self.assertTrue(
            any("owner accepted the quote" in reason for reason in reasons),
            f"the owner's own reason did not survive: {reasons[:5]}")

    def test_the_journals_own_evidence_is_not_redacted_away(self):
        """A control that eats the evidence is worse than the exposure.

        An artifact's digest is a long hex run, which is exactly what the
        redactor treats as a raw key. Redacting `result` as well as `why`
        turned a SOURCE artifact's registration into "a9f[REDACTED]d8a… 104
        bytes" -- a record that can no longer say which file was worked on.
        This asserts the digest the whole audit chain rests on is still there.
        """
        cycle = self.runCycle()
        self.assertTrue(cycle.completed, cycle.why_stopped)
        source_digest = next(
            a.digest for a in self.s.orchestrator.artifacts(cycle.job_id,
                                                            role="SOURCE"))
        results = " ".join(str(row["result"]) for row in
                           self.s.store.raw_readonly(
                               "SELECT result FROM audit_log WHERE job_id = ?",
                               (cycle.job_id,)))
        self.assertIn(source_digest[:16], results,
                      "the source artifact's digest is not in the journal")
        self.assertNotIn("[REDACTED]", results,
                         "the journal's own evidence was redacted")

    def test_the_deliverable_itself_is_untouched(self):
        """The client's file is their data. Solvent does not edit it to make
        its own logs tidier."""
        secret = self.planted()
        source = self.dir / "with-secret.csv"
        source.write_text(f"Name,Note\nAcme Ltd,{secret}\n", encoding="utf-8")
        cycle = self.runCycle(source_file=str(source), agreed_requirements=[
            req("R1", "parses_as_csv"), req("R2", "trim_whitespace")])
        self.assertTrue(cycle.delivered,
                        f"stopped at {cycle.stopped_at}: {cycle.why_stopped}")
        delivered = pathlib.Path(cycle.deliverable_path).read_text(
            encoding="utf-8")
        self.assertIn(secret, delivered,
                      "Solvent altered the client's own content")


class IFixAiTheRefusalMovesEachTimeADefectIsFixed(Cycle):
    """Advisory. Did the control refuse for the *right* reason?

    A test asserting only "it stopped" passes when the stop came from
    somewhere else entirely, and that is how a chain of controls rots into one
    control wearing several names. So: start with several things wrong, fix
    exactly one at a time, and require the refusal to move further down the
    pipeline on each fix. A control that was never doing anything shows up
    here as a stage the refusal skips straight past.
    """

    def stage_index(self, stage: str) -> int:
        return list(CYCLE_STAGES).index(stage) if stage else len(CYCLE_STAGES)

    def test_each_fix_moves_the_refusal_one_control_further(self):
        reached = []

        # 1. Nobody can do the work at all.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board([{**CSV_POSTING, "ref": "chain",
                              "needs": ["video_editing"]}])
        first = self.runCycle(owner_accepts_quote=False,
                              agreed_requirements=None,
                              simulate_payment=False)
        self.assertEqual(first.stopped_at, "qualify")
        self.assertIn("video_editing", first.why_stopped)
        reached.append(first)

        # 2. A capability that can. Now the owner has not answered.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain2.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        second = self.runCycle(owner_accepts_quote=False,
                               agreed_requirements=None,
                               simulate_payment=False)
        self.assertEqual(second.stopped_at, "owner-approval")
        reached.append(second)

        # 3. The owner answers. Now there are no agreed terms.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain3.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        third = self.runCycle(agreed_requirements=None, simulate_payment=False)
        self.assertEqual(third.stopped_at, "contract")
        reached.append(third)

        # 4. Terms, but one names a check nobody performs. Same stage, and it
        #    must be a different reason -- otherwise the contract stage is one
        #    refusal with two labels.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain4.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        fourth = self.runCycle(simulate_payment=False, agreed_requirements=[
            req("R1", "drop_exact_duplicates"), req("R9", "make_it_shine")])
        self.assertEqual(fourth.stopped_at, "contract")
        self.assertNotEqual(fourth.why_stopped, third.why_stopped,
                            "two different defects produced the same refusal")
        self.assertIn("no registered check", fourth.why_stopped)
        self.assertIn("R9", fourth.why_stopped,
                      "the refusal does not say which term it was")

        # 5. Real terms, but the client's destination is not approved.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain5.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        self.s.policy.amend(
            {"egress": {"allowlist": [], "simulation_only": True}},
            OWNER, "test: nowhere is approved")
        fifth = self.runCycle(simulate_payment=False)
        self.assertEqual(fifth.stopped_at, "deliver",
                         f"expected the gate to refuse; got "
                         f"{fifth.stopped_at}: {fifth.why_stopped}")
        self.assertIn("gate", fifth.why_stopped.lower())
        reached.append(fifth)

        # 6. Destination approved. Now nothing has been paid.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain6.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        sixth = self.runCycle(simulate_payment=False)
        self.assertEqual(sixth.stopped_at, "payment")
        self.assertTrue(sixth.delivered)
        reached.append(sixth)

        # 7. Paid. Nothing is left to refuse.
        self.s.store.close()
        self.s = Solvent(str(self.dir / "chain7.db"))
        self.clear_for_client_work()
        self.load_pricing()
        self.register_board()
        seventh = self.runCycle()
        self.assertTrue(seventh.completed,
                        f"stopped at {seventh.stopped_at}: "
                        f"{seventh.why_stopped}")
        reached.append(seventh)

        positions = [self.stage_index(c.stopped_at) for c in reached]
        self.assertEqual(positions, sorted(positions),
                         f"the refusal did not move forward: "
                         f"{[(c.stopped_at or 'complete') for c in reached]}")
        self.assertEqual(len(set(positions)), len(positions),
                         "two different defects were caught by the same "
                         "control, so one of these stages is not a control")

    def test_satisfying_the_gate_does_not_also_satisfy_the_ledger(self):
        """The specific rot this guards: one control standing in for the next.

        A delivered job is not a paid job. If clearing the Action Gate also
        moved the Ledger, a client who never paid would read as revenue.
        """
        cycle = self.runCycle(simulate_payment=False)
        self.assertTrue(cycle.delivered)
        self.assertEqual(cycle.collected_cents, 0)
        self.assertEqual(self.s.ledger.collected_for(cycle.job_id), 0)
        self.assertEqual(self.s.orchestrator.job(cycle.job_id).state,
                         JobState.AWAITING_PAYMENT)

    def test_satisfying_the_ledger_does_not_retrospectively_verify_the_work(self):
        """And the other direction: money arriving does not make a failed
        deliverable acceptable."""
        from solvent.types import PaymentState

        cycle = self.runCycle(simulate_payment=False)
        payment = self.s.ledger.payment_for(cycle.job_id)
        self.s.ledger.set_payment_state(
            payment["id"], PaymentState.PAID,
            collected_cents=cycle.invoiced_cents,
            verification_method="SIMULATED:test")
        ok, why = self.s.orchestrator.verification_satisfied(cycle.job_id)
        self.assertTrue(ok, why)
        evidence = self.s.store.raw_readonly(
            "SELECT * FROM verification_evidence WHERE subject_ref = ?",
            (cycle.job_id,))
        self.assertTrue(evidence, "a job was paid with no verification on record")
