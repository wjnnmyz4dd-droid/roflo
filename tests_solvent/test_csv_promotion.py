"""OD-12: the owner approves csv-cleanup/1.0, and only what was proven.

The decision is the owner's. What these tests establish is that Solvent
records it at the boundary of the evidence rather than at the boundary of the
sentence — approval for CSV cleanup is not approval for spreadsheets, for a
later version, or for the two operations whose checks were never certified.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.capability import Capability, promotion_verdict
from solvent.errors import FailClosed
from solvent.harness import (
    CAPABILITIES, OWNER, Solvent, ensure_verifier_certified, run_csv_job,
)
from solvent.types import Criticality, Requirement, RequirementSource as RS

from tests_solvent import fixtures_csv as fx

#: The nine checks `solvent.csvverify.run` is certified to decide. This is the
#: evidence boundary, and therefore the approval boundary. `rename_headers` and
#: `sort_rows` are implemented operations whose checks the certification
#: battery never exercises, so they are deliberately absent.
CERTIFIED_SCOPE = (
    "drop_exact_duplicates", "map_values", "no_unauthorised_changes",
    "normalise_dates", "parses_as_csv", "preserve_columns", "require_columns",
    "row_reconciliation", "trim_whitespace",
)
UNCERTIFIED_OPERATIONS = ("rename_headers", "sort_rows")

#: Measured, not asserted: 25 black-box scenarios, 0 false completions, levels
#: L1/L2/L3 plus boundary, hostile, trap, client and gap.
CSV_EVIDENCE = Capability(
    name="csv-cleanup", covers=frozenset(CERTIFIED_SCOPE), proven=True,
    version="csv-cleanup/1.0",
    inputs=("text/csv",), outputs=("text/csv",),
    verifiable_by=CERTIFIED_SCOPE,
    proven_levels=("L1", "L2", "L3"),
    evidence_ref="docs/solvent-certification-hardening.md; "
                 "tests_solvent/test_blackbox_certification.py",
    fixtures_passed=25, fixtures_total=25, false_completions=0)


def promoted(solvent: Solvent) -> Solvent:
    """Record the owner's decision through the existing registry path."""
    ensure_verifier_certified(solvent, "csv-cleanup")
    solvent.capability.register(CSV_EVIDENCE, owner_identity=OWNER)
    return solvent


def req(rid, text, check, params=None, source=RS.CLIENT_CONFIRMED_STRUCTURED):
    return Requirement(id=rid, text=text, source=source, acceptance=text,
                       check=check, params=params or {},
                       criticality=Criticality.MANDATORY)


class TheEvidenceSupportsThePromotion(unittest.TestCase):
    """Checked before recording, not after. A prompt is not evidence."""

    def setUp(self):
        self.s = Solvent()

    def test_the_verifier_is_certified_against_the_current_implementation(self):
        from solvent import verifiercert as vc

        record = ensure_verifier_certified(self.s, "csv-cleanup")
        self.assertEqual(record["state"], "CERTIFIED")
        self.assertEqual(
            record["fingerprint"],
            vc.implementation_fingerprint(CAPABILITIES["csv-cleanup"].verify))

    def test_the_evidence_clears_every_promotion_floor(self):
        ok, why = promotion_verdict(CSV_EVIDENCE)
        self.assertTrue(ok, why)

    def test_the_recorded_scope_is_exactly_what_is_certified(self):
        record = ensure_verifier_certified(self.s, "csv-cleanup")
        certified = {c for c in record["certified_checks"].split(",") if c}
        self.assertEqual(set(CSV_EVIDENCE.covers), certified)

    def test_the_uncertified_operations_are_outside_the_scope(self):
        for operation in UNCERTIFIED_OPERATIONS:
            with self.subTest(operation=operation):
                self.assertNotIn(operation, CSV_EVIDENCE.covers)

    def test_zero_false_completions_is_part_of_the_record(self):
        self.assertEqual(CSV_EVIDENCE.false_completions, 0)


class TheOwnerDecisionIsRecorded(unittest.TestCase):
    def setUp(self):
        self.s = promoted(Solvent())

    def test_the_capability_is_registered_as_proven(self):
        proven = [c.name for c in self.s.capability.capabilities() if c.proven]
        self.assertEqual(proven, ["csv-cleanup"])

    def test_the_version_is_recorded(self):
        entry = next(c for c in self.s.capability.capabilities())
        self.assertEqual(entry.version, "csv-cleanup/1.0")

    def test_the_scope_is_recorded(self):
        entry = next(c for c in self.s.capability.capabilities())
        self.assertEqual(set(entry.covers), set(CERTIFIED_SCOPE))

    def test_the_evidence_is_recorded_with_it(self):
        entry = next(c for c in self.s.capability.capabilities())
        self.assertIn("25/25", entry.evidence_summary)
        self.assertIn("0 false completion", entry.evidence_summary)
        self.assertTrue(entry.evidence_ref)

    def test_only_an_owner_could_have_recorded_it(self):
        fresh = Solvent()
        ensure_verifier_certified(fresh, "csv-cleanup")
        for identity in ("capability", "execution", "worker", "relations"):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed):
                    fresh.capability.register(CSV_EVIDENCE,
                                              owner_identity=identity)

    def test_the_decision_is_on_the_audit_record(self):
        events = [e for e in self.s.audit.events()
                  if e["event"] == "capability.registered"]
        self.assertTrue(events)
        self.assertEqual(events[-1]["decision"], "csv-cleanup")
        self.assertTrue(self.s.audit.verify_chain()[0])

    def test_the_registration_history_is_append_only(self):
        history = self.s.capability.registration_history("csv-cleanup")
        self.assertEqual(len(history), 1)
        with self.assertRaises(Exception):
            self.s.store.for_authority("capability").execute(
                "UPDATE registered_capabilities SET proven = 0 WHERE name = ?",
                ("csv-cleanup",))


class NothingElseWasPromoted(unittest.TestCase):
    """CSV approval is CSV approval. It is not a bundle."""

    def setUp(self):
        self.s = promoted(Solvent())

    def test_report_builder_is_not_promoted(self):
        names = [c.name for c in self.s.capability.capabilities() if c.proven]
        self.assertNotIn("report-builder", names)

    def test_report_builder_is_not_registered_at_all(self):
        self.assertNotIn("report-builder",
                         [c.name for c in self.s.capability.capabilities()])

    def test_exactly_one_capability_is_proven(self):
        self.assertEqual(
            len([c for c in self.s.capability.capabilities() if c.proven]), 1)

    def test_no_phantom_capability_became_registered(self):
        registered = {c.name for c in self.s.capability.capabilities()}
        for phantom in ("render_xlsx", "render_pdf", "render_report",
                        "draft_outreach", "translate_text",
                        "pdf_data_extraction", "business_analysis"):
            with self.subTest(phantom=phantom):
                self.assertNotIn(phantom, registered)


class ThePromotionSurvivesARestart(unittest.TestCase):
    """An owner decision that evaporates on restart is not a decision."""

    def setUp(self):
        self.db = str(pathlib.Path(tempfile.mkdtemp()) / "solvent.db")
        self.s = promoted(Solvent(self.db))

    def restart(self) -> Solvent:
        self.s.store.close()
        self.s = Solvent(self.db)
        return self.s

    def test_the_promotion_persists(self):
        restarted = self.restart()
        self.assertEqual(
            [c.name for c in restarted.capability.capabilities() if c.proven],
            ["csv-cleanup"])

    def test_the_scope_persists_exactly(self):
        entry = next(c for c in self.restart().capability.capabilities())
        self.assertEqual(set(entry.covers), set(CERTIFIED_SCOPE))

    def test_the_version_persists(self):
        entry = next(c for c in self.restart().capability.capabilities())
        self.assertEqual(entry.version, "csv-cleanup/1.0")

    def test_the_evidence_binding_persists(self):
        entry = next(c for c in self.restart().capability.capabilities())
        self.assertEqual(entry.fixtures_total, 25)
        self.assertEqual(entry.false_completions, 0)
        self.assertTrue(entry.evidence_ref)

    def test_no_other_capability_becomes_promoted_by_restarting(self):
        restarted = self.restart()
        self.assertEqual(
            len([c for c in restarted.capability.capabilities() if c.proven]), 1)
        self.assertNotIn("report-builder",
                         [c.name for c in restarted.capability.capabilities()])

    def test_the_audit_chain_survives(self):
        self.assertTrue(self.restart().audit.verify_chain()[0])

    def test_a_fresh_database_has_nothing_promoted(self):
        """Guards the tests above: persistence that ignores the database is not
        persistence."""
        other = str(pathlib.Path(tempfile.mkdtemp()) / "other.db")
        self.assertEqual(
            [c.name for c in Solvent(other).capability.capabilities()], [])


class ApprovalDoesNotCrossBoundaries(unittest.TestCase):
    """The four boundaries §24–§26 name, tested through the real path."""

    def setUp(self):
        self.s = promoted(Solvent())
        self.covered = set(CERTIFIED_SCOPE)

    def covers(self, *needs) -> bool:
        return set(needs).issubset(
            {c for cap in self.s.capability.capabilities() for c in cap.covers})

    def test_csv_work_inside_the_proven_scope_is_covered(self):
        self.assertTrue(self.covers("drop_exact_duplicates", "normalise_dates",
                                    "parses_as_csv"))

    def test_an_xlsx_request_is_not_covered_by_csv_approval(self):
        self.assertFalse(self.covers("render_xlsx"))

    def test_a_pdf_request_is_not_covered(self):
        self.assertFalse(self.covers("render_pdf"))

    def test_report_generation_is_not_covered_by_csv_approval(self):
        self.assertFalse(self.covers("report_section", "report_total_correct"))

    def test_profitability_analysis_is_not_covered(self):
        self.assertFalse(self.covers("analyse_profitability"))

    def test_the_two_uncertified_csv_operations_are_not_covered(self):
        for operation in UNCERTIFIED_OPERATIONS:
            with self.subTest(operation=operation):
                self.assertFalse(self.covers(operation))

    def test_an_uncertified_operation_is_refused_by_the_pipeline(self):
        """Not merely out of scope on paper — the job actually fails closed."""
        source, work = fx.workspace(fx.DUPLICATES)
        with self.assertRaises(FailClosed) as caught:
            run_csv_job(source=source, workdir=work, solvent=self.s,
                        client_id="c", title="sort",
                        requirements=[
                            req("R-SORT", "Sort by customer.", "sort_rows",
                                {"columns": ["Customer"]}),
                            req("R-OPEN", "Opens.", "parses_as_csv", {},
                                source=RS.SYSTEM_SAFETY)])
        self.assertIn("not certified to decide", str(caught.exception))

    def test_work_inside_the_scope_still_runs(self):
        """Guards the test above: a capability that refuses everything is useless."""
        source, work = fx.workspace(fx.DUPLICATES)
        report = run_csv_job(source=source, requirements=list(fx.SIMPLE),
                             workdir=work, solvent=self.s, client_id="c",
                             title="in scope")
        self.assertTrue(report.delivered, report.escalated)

    def test_a_different_version_is_not_covered_by_this_approval(self):
        entry = next(c for c in self.s.capability.capabilities())
        self.assertEqual(entry.version, "csv-cleanup/1.0")
        self.assertNotEqual(entry.version, "csv-cleanup/1.1")

    def test_a_changed_verifier_invalidates_the_certification_behind_it(self):
        """Version boundary with teeth: the evidence names the code."""
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="solvent.csvverify.run",
            capability_version="csv-cleanup/1.0", check="drop_exact_duplicates",
            fingerprint="sha256:a-materially-different-implementation")
        self.assertFalse(allowed)
        self.assertIn("different code", why)

    def test_a_later_version_has_no_certification_of_its_own(self):
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref="solvent.csvverify.run",
            capability_version="csv-cleanup/1.1", check="drop_exact_duplicates")
        self.assertFalse(allowed)
        self.assertIn("never been tested", why)


class TheCapabilityGapPathStillWorks(unittest.TestCase):
    """Promotion must not have taught Solvent to say yes to everything."""

    def setUp(self):
        self.s = promoted(Solvent())

    def test_a_request_needing_an_absent_capability_is_refused(self):
        source, work = fx.workspace(fx.DUPLICATES)
        with self.assertRaises(FailClosed):
            run_csv_job(source=source, workdir=work, solvent=self.s,
                        client_id="c", title="pdf",
                        requirements=[
                            req("R-PDF", "Deliver as PDF.", "render_pdf"),
                            req("R-OPEN", "Opens.", "parses_as_csv", {},
                                source=RS.SYSTEM_SAFETY)])

    def test_the_gap_reaches_the_owners_queue_as_a_proposal(self):
        source, work = fx.workspace(fx.DUPLICATES)
        try:
            run_csv_job(source=source, workdir=work, solvent=self.s,
                        client_id="c", title="pdf",
                        requirements=[
                            req("R-PDF", "Deliver as PDF.", "render_pdf"),
                            req("R-OPEN", "Opens.", "parses_as_csv", {},
                                source=RS.SYSTEM_SAFETY)])
        except FailClosed:
            pass
        self.assertIn("render_pdf",
                      [p["name"] for p in self.s.capability.awaiting_owner()])

    def test_a_proposal_is_not_a_capability(self):
        self.test_the_gap_reaches_the_owners_queue_as_a_proposal()
        self.assertNotIn("render_pdf",
                         [c.name for c in self.s.capability.capabilities()])

    def test_promotion_did_not_enable_unattended_operation(self):
        self.assertTrue(self.s.policy.get("egress", "simulation_only",
                                          default=False))
        self.assertEqual(
            self.s.store.raw_readonly("SELECT * FROM action_requests"), [])
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)


class CustomerServiceDoesNotOverstateTheCapability(unittest.TestCase):
    def setUp(self):
        self.s = promoted(Solvent())
        self.source, work = fx.workspace(fx.DUPLICATES)
        self.report = run_csv_job(source=self.source,
                                  requirements=list(fx.SIMPLE), workdir=work,
                                  solvent=self.s, client_id="client:ask",
                                  title="ask")

    def ask(self, body):
        return self.s.relations.handle(job_id=self.report.job_id, body=body,
                                       source=self.source).body.lower()

    def test_it_does_not_promise_xlsx(self):
        reply = self.ask("Can you also do this for my Excel workbook?")
        for promise in ("yes, we can", "we can do that", "no problem",
                        "we'll handle", "of course"):
            self.assertNotIn(promise, reply)

    def test_it_does_not_promise_pdf(self):
        reply = self.ask("Can you deliver this as a PDF as well?")
        for promise in ("yes, we can", "we can do that", "no problem"):
            self.assertNotIn(promise, reply)

    def test_it_does_not_promise_analysis(self):
        reply = self.ask("Also analyse which customers are most profitable.")
        for promise in ("yes, we can", "we can do that", "no problem"):
            self.assertNotIn(promise, reply)

    def test_scope_creep_does_not_silently_become_work(self):
        digest = self.s.orchestrator.current_deliverable(
            self.report.job_id).digest
        self.ask("Also analyse which customers are most profitable.")
        self.assertEqual(
            self.s.orchestrator.current_deliverable(self.report.job_id).digest,
            digest)

    def test_every_out_of_scope_request_reaches_a_person(self):
        for body in ("Can you also do my Excel workbook?",
                     "Deliver this as a PDF too.",
                     "Analyse profitability for me."):
            handled = self.s.relations.handle(job_id=self.report.job_id,
                                              body=body, source=self.source)
            with self.subTest(body=body):
                self.assertIn(handled.routed_to,
                              ("OWNER", "REQUALIFY", "REMEDIATION", "NONE"))


if __name__ == "__main__":
    unittest.main()
