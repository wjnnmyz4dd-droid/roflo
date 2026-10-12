"""The five-skill library: registered, certified against evidence, and bounded.

This file is the certification record. It is a test rather than a document
because a certification that exists only in a report is a certification nobody
re-runs, and the thing being claimed -- *this* code, at *this* fingerprint,
caught every modelled defect over enough distinct instances to mean something
-- stops being true the moment a verifier is edited.

Four questions:

* **Is the library what it claims to be?** Five capabilities, each with a
  worker, a verifier that does not import it, and a registry entry so the
  dispatch table can find it.
* **Does each verifier earn its certification?** Through the production path,
  against generated evidence, with no false accepts and no false rejects, and
  with each check clearing the evidence floor. A check nobody put a defect in
  front of is not certified, however many correct artifacts it approved.
* **Is it still right about evidence it was not shown?** A third seed, a
  surprise battery. A verifier tuned until the certification stream passes has
  demonstrated that it passes the certification stream.
* **Does the Skills Lab lifecycle hold?** Each new skill goes need -> overlap
  -> evidence -> specified -> built -> certified, and stops there. Promotion is
  the owner's and nothing here takes it.

What this file does **not** claim: that any of the five may be used for client
work. Certification is evidence about a verifier; promotion is a decision about
a business. The last class asserts the gap, because the gap is the control.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import certgen, checks, skillslab as lab, verifiercert
from solvent.capability import Capability
from solvent.errors import FailClosed
from solvent.harness import (
    CAPABILITIES, OWNER, UNCERTIFIABLE, Solvent, certifiable_scope,
    certification_cases, ensure_verifier_certified, provision_development_only,
    verifier_ref,
)

#: The library, and the single sentence that says what each one is for. Written
#: out so that adding a sixth has to be a deliberate edit here rather than a
#: dict that quietly grew.
LIBRARY = {
    "csv-cleanup": "tidy a delimited file to a stated specification",
    "report-builder": "render supplied facts as a document, inventing none",
    "spreadsheet-automation": "transform a workbook by arithmetic nobody guessed",
    "data-entry": "map records into a schema and hold the ones that fail its rules",
    "document-processing": "extract, convert and format documents without "
                           "inventing their content",
}

#: What each capability reads. The overlap gate needs it: the same check name
#: over a different format is a different capability, and without the domain
#: the gate can only see that two skills both have a check called
#: ``trim_whitespace``.
READS = {
    "csv-cleanup": ("text/csv",),
    "report-builder": ("application/json",),
    "spreadsheet-automation": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    "data-entry": ("text/csv", "application/json"),
    "document-processing": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain", "text/markdown"),
}

#: Each capability's worker and verifier module, so the independence assertion
#: names them rather than inferring them.
PAIRS = {
    "csv-cleanup": ("solvent.csvwork", "solvent.csvverify"),
    "report-builder": ("solvent.reportwork", "solvent.reportverify"),
    "spreadsheet-automation": ("solvent.sheetwork", "solvent.sheetverify"),
    "data-entry": ("solvent.entrywork", "solvent.entryverify"),
    "document-processing": ("solvent.docwork", "solvent.docverify"),
}


class TheLibraryIsFive(unittest.TestCase):

    def test_exactly_five_capabilities_are_registered(self):
        self.assertEqual(sorted(CAPABILITIES), sorted(LIBRARY))

    def test_each_one_has_a_version_naming_itself(self):
        for name, spec in CAPABILITIES.items():
            with self.subTest(name=name):
                self.assertTrue(spec.version.startswith(f"{name}/"),
                                f"{name} is versioned {spec.version!r}")

    def test_each_one_is_in_the_dispatch_table(self):
        for name in LIBRARY:
            with self.subTest(name=name):
                self.assertTrue(checks.known(name))
                self.assertTrue(checks.checks_for(name))
                self.assertIsNotNone(checks.runner_for(name))

    def test_the_dispatch_table_holds_nothing_else(self):
        self.assertEqual(sorted(checks.REGISTRY), sorted(LIBRARY))

    def test_a_verifier_does_not_import_the_worker_it_checks(self):
        """The independence is structural, not a matter of discipline."""
        import importlib

        for name, (work, verify) in PAIRS.items():
            with self.subTest(name=name):
                module = importlib.import_module(verify)
                source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
                leaf = work.rsplit(".", 1)[1]
                self.assertNotIn(f"from . import {leaf}", source,
                                 f"{verify} imports {work}")
                self.assertNotIn(f"import {work}", source,
                                 f"{verify} imports {work}")

    def test_each_registered_verifier_is_the_one_in_the_dispatch_table(self):
        for name, spec in CAPABILITIES.items():
            with self.subTest(name=name):
                self.assertIs(spec.verify, checks.runner_for(name),
                              "two tables disagree about which code verifies "
                              f"{name}, which is one authority too many")

    def test_every_generated_battery_knows_this_capability(self):
        for name in LIBRARY:
            with self.subTest(name=name):
                trials = certgen.generate(name, seed=1, cases=1)
                self.assertTrue(trials, f"no battery generates cases for {name}")


class Certification(unittest.TestCase):
    """The production path, on a real database, against generated evidence."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.s = Solvent(str(pathlib.Path(cls.dir.name) / "library.db"))
        cls.records = {name: ensure_verifier_certified(cls.s, name)
                       for name in LIBRARY}

    @classmethod
    def tearDownClass(cls):
        try:
            cls.s.store.close()
        finally:
            cls.dir.cleanup()

    def certified(self, name) -> set:
        return {c for c in self.records[name]["certified_checks"].split(",") if c}

    def test_every_verifier_reaches_certified(self):
        for name in LIBRARY:
            with self.subTest(name=name):
                self.assertEqual(self.records[name]["state"], "CERTIFIED",
                                 self.records[name]["why"])

    def test_every_certification_names_the_code_it_was_earned_by(self):
        for name, spec in CAPABILITIES.items():
            with self.subTest(name=name):
                recorded = self.records[name]["fingerprint"]
                self.assertTrue(recorded)
                self.assertEqual(
                    recorded, verifiercert.implementation_fingerprint(spec.verify),
                    "the certification is about different code")
                self.assertEqual(self.records[name]["verifier_ref"],
                                 verifier_ref(spec))

    def test_the_certified_scope_is_not_empty_and_not_everything(self):
        """A check is certified when evidence covers it, and not otherwise."""
        for name in LIBRARY:
            with self.subTest(name=name):
                certified = self.certified(name)
                self.assertTrue(certified, f"{name} is certified on nothing")
                self.assertTrue(
                    certified <= set(checks.checks_for(name)),
                    f"{name} is certified on a check its verifier does not list")

    def test_the_new_capabilities_are_certified_on_their_load_bearing_checks(self):
        """Named individually, because these are the ones a client relies on."""
        required = {
            "spreadsheet-automation": {
                "compute_column", "formulas_agree", "total_row",
                "row_reconciliation", "no_unauthorised_changes"},
            "data-entry": {
                "exceptions_justified", "records_reconcile",
                "no_invented_values", "record_traceability", "map_fields",
                "validate_type", "validate_range", "validate_enum",
                "validate_pattern", "require_field"},
            "document-processing": {
                "extract_fields", "fields_traceable", "absences_disclosed",
                "no_invented_content", "table_preserved",
                "unsupported_declared"},
        }
        for name, wanted in required.items():
            with self.subTest(name=name):
                missing = sorted(wanted - self.certified(name))
                self.assertEqual(missing, [],
                                 f"{name} is not certified on {missing}")

    def test_no_false_accept_and_no_false_reject_anywhere(self):
        for name in LIBRARY:
            with self.subTest(name=name):
                report = verifiercert.run_generated(
                    CAPABILITIES[name].verify, capability=name,
                    verifier_ref=verifier_ref(CAPABILITIES[name]),
                    seed=certgen.CERTIFICATION_SEED,
                    cases=certification_cases(name))
                self.assertEqual(
                    [r.trial.name for r in report.false_accepts], [],
                    "defective work was accepted")
                self.assertEqual(
                    [r.trial.name for r in report.false_rejects], [],
                    "correct work was refused")

    def test_every_certified_check_clears_the_evidence_floor(self):
        """Ten distinct correct artifacts, every defect class, five of each."""
        registry = self.s.capability
        for name in LIBRARY:
            report = verifiercert.run_generated(
                CAPABILITIES[name].verify, capability=name,
                verifier_ref=verifier_ref(CAPABILITIES[name]),
                seed=certgen.CERTIFICATION_SEED,
                    cases=certification_cases(name))
            for check in sorted(self.certified(name)):
                with self.subTest(name=name, check=check):
                    attempted = report.classes_attempted(check)
                    instances = report.class_instances(check)
                    self.assertGreaterEqual(
                        report.good_instances(check),
                        registry.MIN_GOOD_INSTANCES,
                        "too few distinct correct artifacts")
                    self.assertTrue(
                        attempted,
                        "no defect was ever put in front of it, so it has "
                        "shown only that it says yes")
                    thin = sorted(c for c in attempted
                                  if instances.get(c, 0)
                                  < registry.MIN_INSTANCES_PER_DEFECT)
                    self.assertEqual(thin, [], "demonstrated on too few "
                                               "distinct instances")

    def test_an_edited_verifier_loses_the_certification_it_earned(self):
        """Trust names code. Rename the function and it is a different subject."""
        name = "data-entry"
        spec = CAPABILITIES[name]
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref=verifier_ref(spec), capability_version=spec.version,
            check="exceptions_justified",
            fingerprint=verifiercert.implementation_fingerprint(spec.verify))
        self.assertTrue(allowed, why)
        refused, why = self.s.capability.may_authorize_delivery(
            verifier_ref=verifier_ref(spec), capability_version=spec.version,
            check="exceptions_justified", fingerprint="a different implementation")
        self.assertFalse(refused)
        self.assertIn("must be re-earned", why)

    def test_trust_does_not_spread_between_capabilities(self):
        spec = CAPABILITIES["data-entry"]
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref=verifier_ref(spec),
            capability_version="document-processing/1.0",
            check="extract_fields")
        self.assertFalse(allowed)
        self.assertIn("never been tested", why)

    def test_a_check_outside_the_certified_scope_is_refused(self):
        spec = CAPABILITIES["spreadsheet-automation"]
        allowed, why = self.s.capability.may_authorize_delivery(
            verifier_ref=verifier_ref(spec), capability_version=spec.version,
            check="number_format")
        self.assertFalse(allowed, "a check no battery can ever cover")
        self.assertIn("not certified to decide", why)


class WhatEvidenceCannotCover(unittest.TestCase):
    """The limits, named, rather than quietly claimed."""

    def test_the_uncertifiable_checks_are_named_with_a_reason(self):
        for capability, entries in UNCERTIFIABLE.items():
            with self.subTest(capability=capability):
                self.assertTrue(entries)
                for check, why in entries.items():
                    self.assertIn(check, checks.checks_for(capability),
                                  "a check nobody lists needs no exemption")
                    self.assertGreater(len(why), 60,
                                       f"{check} is excluded without saying why")

    def test_the_certifiable_scope_is_narrower_than_the_check_list(self):
        self.assertEqual(
            sorted(set(checks.checks_for("spreadsheet-automation"))
                   - certifiable_scope("spreadsheet-automation")),
            ["number_format"])

    def test_the_other_four_have_nothing_excluded(self):
        for name in LIBRARY:
            if name == "spreadsheet-automation":
                continue
            with self.subTest(name=name):
                self.assertEqual(certifiable_scope(name),
                                 frozenset(checks.checks_for(name)))

    def test_the_battery_never_grades_an_uncertifiable_check(self):
        """Otherwise a correct verifier fails trials it cannot pass."""
        for capability, entries in UNCERTIFIABLE.items():
            trials = certgen.generate(capability, seed=1, cases=3)
            graded = {t.check for t in trials}
            for check in entries:
                with self.subTest(capability=capability, check=check):
                    self.assertNotIn(check, graded)

    def test_a_promotion_wider_than_the_evidence_is_refused(self):
        from solvent.capability import Capability
        from solvent.errors import FailClosed
        from solvent.harness import provision_capability

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        s = Solvent(str(pathlib.Path(directory.name) / "wide.db"))
        self.addCleanup(lambda: s.store.close())
        everything = frozenset(checks.checks_for("spreadsheet-automation"))
        with self.assertRaises(FailClosed) as caught:
            provision_capability(
                s, Capability(name="spreadsheet-automation", covers=everything,
                              proven=True, version="spreadsheet-automation/1.0",
                              verifiable_by=tuple(sorted(everything))),
                owner_identity=OWNER, record_decision=False)
        message = str(caught.exception)
        self.assertIn("evidence no longer supports this scope", message)
        self.assertIn("number_format", message)
        self.assertIn("Nothing was registered", message)


class TheSurpriseBattery(unittest.TestCase):
    """A third seed. A verifier tuned until certification passes has shown only
    that certification passes."""

    def test_every_verifier_survives_evidence_it_was_not_shown(self):
        for name in LIBRARY:
            with self.subTest(name=name):
                report = verifiercert.run_generated(
                    CAPABILITIES[name].verify, capability=name,
                    verifier_ref=f"surprise.{name}",
                    seed=certgen.SURPRISE_SEED, cases=10, stream="surprise")
                self.assertTrue(report.results, "the surprise stream was empty")
                self.assertEqual([r.trial.name for r in report.false_accepts], [])
                self.assertEqual([r.trial.name for r in report.false_rejects], [])

    def test_the_three_streams_are_different_evidence(self):
        for name in LIBRARY:
            with self.subTest(name=name):
                digests = []
                for seed in (certgen.DEVELOPMENT_SEED,
                             certgen.CERTIFICATION_SEED,
                             certgen.SURPRISE_SEED):
                    trials = certgen.generate(name, seed=seed, cases=3)
                    digests.append(tuple(
                        (t.source_text, t.source_bytes, t.output_text,
                         t.output_bytes) for t in trials))
                self.assertEqual(len(set(digests)), 3,
                                 "two streams generate the same cases")


class TheSkillsLabLifecycle(unittest.TestCase):
    """Each new skill through the lifecycle, and stopped at the owner."""

    NEW = ("spreadsheet-automation", "data-entry", "document-processing")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.s = Solvent(str(pathlib.Path(self.dir.name) / "lab.db"))
        self.addCleanup(self.close)
        self.s.capability.register(
            Capability(name="csv-cleanup", covers=frozenset(
                checks.checks_for("csv-cleanup")), proven=True,
                version="csv-cleanup/1.0", inputs=READS["csv-cleanup"],
                outputs=("text/csv",),
                verifiable_by=tuple(checks.checks_for("csv-cleanup")),
                proven_levels=("L1", "L2", "L3"), fixtures_passed=34,
                fixtures_total=34, false_completions=0,
                evidence_ref="tests_solvent/test_blackbox_certification.py"),
            owner_identity=OWNER)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def through_the_lifecycle(self, name: str) -> tuple[str, dict]:
        """need -> overlap -> evidence -> specified -> built -> certified."""
        covers = certifiable_scope(name)
        project_id = self.s.skillslab.record_need(
            skill=name, need=LIBRARY[name], observed_on=f"library:{name}",
            occurrences=1)
        verdict, why = self.s.skillslab.check_overlap(
            project_id=project_id, target_covers=covers,
            target_inputs=READS[name])
        # The owner's answer to "may this be built and tested?". The Lab
        # refuses to specify a skill nobody decided should exist, which is the
        # control rather than a step to route around: four separate questions
        # have to hold before a line is written, and this is the first.
        provision_development_only(self.s, name, covers, owner_identity=OWNER)
        claim = self.s.skillslab.record_claim(
            project_id=project_id,
            claim=f"the {name} verifier decides {len(covers)} named checks",
            source=f"{PAIRS[name][1]}.CHECKS, read directly",
            trust=lab.MEASURED)
        self.s.skillslab.verify_claim(
            claim_id=claim, status=lab.VERIFIED, checked_by="skillslab",
            detail="the battery exercised each one against hidden ground truth")
        self.s.skillslab.mark_specified(
            project_id=project_id, target_covers=covers,
            target_version=CAPABILITIES[name].version)
        record = ensure_verifier_certified(self.s, name)
        fingerprint = record["fingerprint"]
        self.s.skillslab.mark_built(project_id=project_id,
                                    fingerprint=fingerprint)
        certified = frozenset(
            c for c in record["certified_checks"].split(",") if c)
        return project_id, {"verdict": verdict, "why": why, "record": record,
                            "certified": certified, "covers": covers,
                            "fingerprint": fingerprint}

    def test_each_new_skill_is_genuinely_new_rather_than_an_existing_one(self):
        for name in self.NEW:
            with self.subTest(name=name):
                _, state = self.through_the_lifecycle(name)
                self.assertEqual(state["verdict"], lab.NEW_SKILL_REQUIRED,
                                 state["why"])

    def test_a_skill_reaches_certified_only_for_the_scope_the_evidence_covers(self):
        for name in self.NEW:
            with self.subTest(name=name):
                project_id, state = self.through_the_lifecycle(name)
                ok, detail = self.s.skillslab.record_certification(
                    project_id=project_id, state=state["record"]["state"],
                    certified_checks=state["certified"],
                    fingerprint=state["fingerprint"],
                    verifier_fingerprint=state["fingerprint"],
                    evidence_ref=f"certgen seed {certgen.CERTIFICATION_SEED}")
                uncovered = sorted(state["covers"] - state["certified"])
                if uncovered:
                    # A scope wider than the evidence does not reach the owner,
                    # and that refusal is the control rather than a problem to
                    # work around: asking the owner to approve what the
                    # evidence does not cover is asking them to be the evidence.
                    self.assertFalse(ok)
                    self.assertIn("not certified for", detail)
                else:
                    self.assertTrue(ok, detail)
                    self.assertEqual(
                        self.s.skillslab.project(project_id).stage, lab.CERTIFIED)

    def test_a_certified_skill_is_offered_and_not_promoted(self):
        name = "data-entry"
        project_id, state = self.through_the_lifecycle(name)
        ok, detail = self.s.skillslab.record_certification(
            project_id=project_id, state="CERTIFIED",
            certified_checks=state["certified"],
            fingerprint=state["fingerprint"])
        if not ok:
            self.skipTest(f"scope wider than the evidence: {detail}")
        self.assertEqual(self.s.skillslab.offer_to_owner(project_id),
                         lab.AWAITING_OWNER)
        self.assertEqual([p.id for p in self.s.skillslab.awaiting_owner()],
                         [project_id])

    def test_the_lab_cannot_promote_what_it_certified(self):
        name = "document-processing"
        project_id, state = self.through_the_lifecycle(name)
        self.s.skillslab.record_certification(
            project_id=project_id, state="CERTIFIED",
            certified_checks=state["certified"],
            fingerprint=state["fingerprint"])
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_promotion(
                project_id=project_id, owner_identity="skillslab",
                why="it certified cleanly", fingerprint=state["fingerprint"])
        self.assertIn("not a registered owner", str(caught.exception))

    def test_without_the_input_domain_a_spreadsheet_skill_looks_like_a_widening(self):
        """The gate's narrower answer has to be asked for, never defaulted to.

        Stated as a test because it is the one way this change could have
        weakened the gate: if omitting the domain produced NEW_SKILL_REQUIRED,
        a second capability over the same authority would get through on a
        missing field.
        """
        name = "spreadsheet-automation"
        covers = certifiable_scope(name)
        project_id = self.s.skillslab.record_need(
            skill=name, need=LIBRARY[name], observed_on="library")
        verdict, why = self.s.skillslab.check_overlap(
            project_id=project_id, target_covers=covers)
        self.assertEqual(verdict, lab.UPGRADE_EXISTING)
        self.assertIn("csv-cleanup", why)

    def test_a_shared_input_domain_is_still_an_overlap(self):
        """Not a licence to declare a new skill by naming a format."""
        name = "csv-tidy-again"
        covers = frozenset(checks.checks_for("csv-cleanup"))
        project_id = self.s.skillslab.record_need(
            skill=name, need="clean CSV files, again", observed_on="library")
        verdict, why = self.s.skillslab.check_overlap(
            project_id=project_id, target_covers=covers,
            target_inputs=("text/csv",))
        self.assertEqual(verdict, lab.EXISTING_SUFFICIENT, why)

    def test_an_unstated_domain_on_the_existing_side_is_not_a_difference(self):
        from solvent.capability import Capability

        # A check csv-cleanup does not cover, so this capability is the only
        # one the gate can be comparing against -- and it records no input
        # domain at all, which is the case under test.
        self.s.capability.register(
            Capability(name="legacy-thing", covers=frozenset({"compute_column"}),
                       proven=True, version="legacy-thing/1.0",
                       verifiable_by=("compute_column",),
                       proven_levels=("L1", "L2", "L3"), fixtures_passed=8,
                       fixtures_total=8, evidence_ref="test"),
            owner_identity=OWNER)
        project_id = self.s.skillslab.record_need(
            skill="computer", need="compute things", observed_on="library")
        verdict, _ = self.s.skillslab.check_overlap(
            project_id=project_id, target_covers=frozenset({"compute_column"}),
            target_inputs=("application/x-made-up",))
        self.assertIn(verdict, (lab.EXISTING_SUFFICIENT, lab.UPGRADE_EXISTING),
                      "a capability with no recorded input domain was treated "
                      "as reading something different, which would let a "
                      "second authority through on a missing field")

    def test_certification_cannot_skip_the_build(self):
        name = "spreadsheet-automation"
        covers = certifiable_scope(name)
        project_id = self.s.skillslab.record_need(
            skill=name, need=LIBRARY[name], observed_on="library")
        self.s.skillslab.check_overlap(project_id=project_id,
                                       target_covers=covers)
        with self.assertRaises(FailClosed) as caught:
            self.s.skillslab.record_certification(
                project_id=project_id, state="CERTIFIED",
                certified_checks=covers, fingerprint="whatever")
        self.assertIn("certification follows a build", str(caught.exception))


class TheWholePipelineRunsEachCapability(unittest.TestCase):
    """A real job, end to end, for each capability that was just added.

    Everything above tests the halves. This runs the actual pipeline -- intake,
    plan, worker, verifier, delivery gate -- because the defects that cost the
    most are the ones where two unit-tested halves were never introduced. Two
    came out of writing this class: the worker read a requirement field the
    orchestrator does not supply, and the verification loop handed each check
    its own parameters and not the brief, so every property of the whole job
    answered UNVERIFIABLE and blocked delivery with no visible cause.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)
        self.s = Solvent(str(self.root / "pipeline.db"))
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def permit(self, name: str):
        from solvent.capability import Capability

        covers = certifiable_scope(name)
        return Capability(name=name, covers=covers, version=f"{name}/1.0",
                          proven=True, verifiable_by=tuple(sorted(covers)))

    def run_job(self, name, source, requirements):
        from solvent.harness import run_csv_job

        return run_csv_job(
            source=str(source), requirements=requirements,
            workdir=str(self.root), solvent=self.s, client_id="client:pipeline",
            title=f"pipeline-{name}", capability=name,
            provision=self.permit(name))

    def requirement(self, rid, check, params=None):
        from solvent.types import Criticality, Requirement, RequirementSource

        return Requirement(
            id=rid, text=check,
            source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
            acceptance=check, check=check, params=params or {},
            criticality=Criticality.MANDATORY)

    def test_a_spreadsheet_job_is_delivered_and_verified(self):
        from solvent import xlsx

        source = self.root / "client.xlsx"
        source.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Data",
            rows=[["Item", "Client ref", "Units", "Rate"],
                  ["  Support ", "CR-2001", 4, "25.50"],
                  ["Consulting", "CR-2002", 2, "100.00"]])]))
        report = self.run_job("spreadsheet-automation", source, [
            self.requirement("R1", "require_columns",
                             {"columns": ["Item", "Units", "Rate"]}),
            self.requirement("R2", "trim_whitespace", {"columns": ["Item"]}),
            self.requirement("R3", "compute_column",
                             {"into": "Amount", "expression": "product",
                              "using": ["Units", "Rate"]}),
            self.requirement("R4", "total_row",
                             {"aggregates": {"Amount": "sum"}}),
            self.requirement("R5", "sort_rows", {"columns": ["Item"]})])
        self.assertTrue(report.delivered, report.escalated[:400])
        delivered = self.s.orchestrator.current_deliverable(report.job_id)
        book = xlsx.read_workbook(pathlib.Path(delivered.path).read_bytes())
        values = [[cell.value for cell in row] for row in book.first.rows]
        self.assertEqual(values[0][-1], "Amount")
        self.assertEqual(values[-1][0], "TOTAL")
        self.assertEqual(values[-1][-1], 302.0)

    def test_a_data_entry_job_is_delivered_with_its_exceptions(self):
        source = self.root / "records.csv"
        source.write_text(
            "Ref,Client Name,Invoiced,Due,Status\n"
            "A-1001,  Acme   Corp ,1250.00,2026-03-01,open\n"
            "A-1002,Borealis GmbH,480.50,2026-03-15,paid\n"
            "A-1003,,92.00,2026-04-02,open\n", encoding="utf-8")
        report = self.run_job("data-entry", source, [
            self.requirement("R1", "map_fields", {"mapping": {
                "Reference": "Ref", "Client": "Client Name",
                "Amount": "Invoiced", "Due Date": "Due", "State": "Status"}}),
            self.requirement("R2", "normalise_whitespace",
                             {"fields": ["Client"]}),
            self.requirement("R3", "require_field",
                             {"fields": ["Reference", "Client"]}),
            self.requirement("R4", "validate_type",
                             {"types": {"Amount": "decimal",
                                        "Due Date": "date"}}),
            self.requirement("R5", "records_reconcile", {}),
            self.requirement("R6", "exceptions_justified", {}),
            self.requirement("R7", "record_traceability", {})])
        self.assertTrue(report.delivered, report.escalated[:400])
        delivered = pathlib.Path(
            self.s.orchestrator.current_deliverable(report.job_id).path)
        text = delivered.read_text(encoding="utf-8")
        self.assertIn("Acme Corp", text)
        self.assertNotIn("A-1003", text, "the record with no client is held")
        exceptions = delivered.with_name(delivered.stem + "-exceptions.csv")
        self.assertTrue(exceptions.exists(),
                        "a data-entry deliverable is two files")
        self.assertIn("require_field", exceptions.read_text(encoding="utf-8"))

    def test_a_document_job_is_delivered_and_verified(self):
        from solvent import docx

        source = self.root / "invoice.docx"
        source.write_bytes(docx.write_document([
            docx.Out(text="Invoice", style="Title"),
            docx.Out(text="Invoice Number: A-1001"),
            docx.Out(text="Client: Acme Corp"),
            docx.Out(kind="table", rows=(("Item", "Amount"),
                                         ("Consulting", "1,000.00")))]))
        report = self.run_job("document-processing", source, [
            self.requirement("R1", "extract_fields", {"labels": {
                "Invoice Number": ["Invoice Number"],
                "Client": ["Client"],
                "Purchase Order": ["Purchase Order"]}}),
            self.requirement("R2", "extract_table",
                             {"columns": ["Item", "Amount"]}),
            self.requirement("R3", "fields_traceable", {}),
            self.requirement("R4", "absences_disclosed", {}),
            self.requirement("R5", "no_invented_content", {})])
        self.assertTrue(report.delivered, report.escalated[:400])
        import json as _json

        payload = _json.loads(pathlib.Path(
            self.s.orchestrator.current_deliverable(report.job_id).path
        ).read_text(encoding="utf-8"))
        values = {f["field"]: f["value"] for f in payload["fields"]}
        self.assertEqual(values["Invoice Number"], "A-1001")
        self.assertEqual(values["Purchase Order"], "NOT FOUND IN SOURCE")
        self.assertEqual(payload["tables"][0][1], ["Consulting", "1,000.00"])


class NoneOfThisIsPermissionToSell(unittest.TestCase):
    """Certification is evidence about a verifier. Promotion is a decision."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.s = Solvent(str(pathlib.Path(self.dir.name) / "scope.db"))
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_certifying_a_verifier_registers_no_capability(self):
        for name in ("spreadsheet-automation", "data-entry",
                     "document-processing"):
            ensure_verifier_certified(self.s, name)
        registered = {c.name for c in self.s.capability.capabilities()}
        self.assertEqual(registered & set(LIBRARY), set(),
                         "a certification registered a capability, which "
                         "would make the evidence its own permission")

    def test_the_three_new_skills_are_not_owner_promoted_in_this_codebase(self):
        """``_owner_promoted`` is where a deployment's promotions live."""
        from solvent.harness import _owner_promoted

        for name in ("spreadsheet-automation", "data-entry",
                     "document-processing", "report-builder"):
            with self.subTest(name=name):
                self.assertIsNone(_owner_promoted(name),
                                  f"{name} is recorded as owner-promoted; "
                                  "promotion is the owner's act, not a build's")

    def test_the_four_unpromoted_skills_are_recorded_as_development_only(self):
        """Two different sentences, and the registry keeps them apart.

        "The owner said build and test this" and "the owner said sell this"
        are not the same decision. LIMITED is the first; nothing here is the
        second.
        """
        from solvent.harness import _DEVELOPMENT_ONLY

        self.assertEqual(
            sorted(_DEVELOPMENT_ONLY),
            ["data-entry", "document-processing", "report-builder",
             "spreadsheet-automation"])
        for name, why in _DEVELOPMENT_ONLY.items():
            with self.subTest(name=name):
                self.assertIn("not cleared", why)

    def test_a_development_decision_is_limited_and_not_approved(self):
        name = "spreadsheet-automation"
        covers = certifiable_scope(name)
        provision_development_only(self.s, name, covers, owner_identity=OWNER)
        decision, why = self.s.capability.development_decision(name)
        self.assertEqual(decision, self.s.capability.LIMITED, why)
        may_deploy, why = self.s.capability.may_deploy(name)
        self.assertFalse(may_deploy,
                         "a LIMITED decision authorised deployment")

    def test_only_csv_cleanup_carries_an_owner_promotion(self):
        from solvent.harness import _owner_promoted

        promoted = [n for n in LIBRARY if _owner_promoted(n) is not None]
        self.assertEqual(promoted, ["csv-cleanup"])


if __name__ == "__main__":
    unittest.main()
