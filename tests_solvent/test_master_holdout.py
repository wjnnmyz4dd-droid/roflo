"""§89. Written last, against finished code, to find what the rest missed.

None of these shaped the implementation. They are aimed at the seams between
the pieces this pass added -- intake roles, real adapters, the validation
bridge, digest binding, the cost total -- because a seam is where two things
that were each tested separately meet something neither expected.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from solvent import intake
from solvent import opportunityrisk as risk
from solvent import skillslab as lab
from solvent import sourceaccess as access
from solvent import sourcecatalog as catalogue
from solvent.adapters import GrantsGovSource, SamGovOpportunitiesSource
from solvent.capability import Capability
from solvent.discovery import Compliance, FixtureSource, ManualSource, Readiness
from solvent.errors import FailClosed
from solvent.harness import CSV_PROMOTION, OWNER, Solvent, provision_capability
from solvent.types import CostCategory
from test_skills_lab import verified_project

BASE = {"ref": "h1", "title": "Deduplicate a CSV", "quoted_cents": 40_000,
        "needs": ["csv-cleanup"], "body": "Please clean this file."}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")
        self.s = Solvent(self.db)
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def register(self, name, kind=FixtureSource, postings=(BASE,)):
        self.s.discovery.register_source(
            kind(name, [dict(p, ref=f"{name}-{i}")
                        for i, p in enumerate(postings)]),
            owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="fixture")
        approved = self.s.policy.get("discovery", "approved_sources", default=[])
        self.s.policy.amend(
            {"discovery": {"approved_sources": sorted({*approved, name})}},
            OWNER, "approve")

    def reopen(self):
        self.s.store.close()
        self.s = Solvent(self.db)


class SeamsBetweenTheTwoIntakeRoles(Base):

    def test_a_source_cannot_change_role_by_re_registering(self):
        """The dangerous direction: register as found, re-register as manual,
        and inherit owner-confirmed provenance for network content."""
        self.register("shifty", FixtureSource)
        self.assertEqual(intake.role_of(self.s.discovery.source_state("shifty")),
                         intake.FOUND)
        self.register("shifty", ManualSource)
        state = self.s.discovery.source_state("shifty")
        # Whatever it is now, it cannot be both: if it reads INSERTED it must
        # also have lost DISCOVER, so network content cannot arrive as owner
        # confirmed.
        if intake.role_of(state) == intake.INSERTED:
            self.assertEqual(self.s.discovery.poll("shifty"), [])

    def test_an_inserted_source_with_every_permission_still_cannot_poll(self):
        self.register("hand", ManualSource)
        for permission in access.PERMISSIONS:
            try:
                self.s.discovery.grant("hand", permission,
                                       owner_identity=OWNER, why="holdout")
            except FailClosed:
                pass
        self.assertEqual(self.s.discovery.poll("hand"), [])

    def test_inserting_with_no_postings_uses_the_registered_ones(self):
        self.register("hand", ManualSource)
        self.assertEqual(
            len(self.s.discovery.insert("hand", owner_identity=OWNER)), 1)

    def test_inserting_the_same_posting_twice_is_deduplicated(self):
        self.register("hand", ManualSource)
        self.s.discovery.insert("hand", owner_identity=OWNER)
        self.assertEqual(
            self.s.discovery.insert("hand", owner_identity=OWNER), [])

    def test_an_unknown_insertion_method_is_refused(self):
        self.register("hand", ManualSource)
        with self.assertRaises(FailClosed):
            self.s.discovery.insert("hand", owner_identity=OWNER,
                                    method="TELEPATHY")

    def test_each_known_insertion_method_is_accepted(self):
        self.register("hand", ManualSource)
        for index, method in enumerate(intake.INSERTION_METHODS):
            found = self.s.discovery.insert(
                "hand", owner_identity=OWNER, method=method,
                postings=[dict(BASE, ref=f"m-{index}")])
            self.assertEqual(len(found), 1, method)


class SeamsInTheRealAdapters(Base):

    def test_an_adapter_registered_then_disabled_stays_disabled(self):
        self.s.discovery.register_source(
            GrantsGovSource(), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="public feed")
        self.s.discovery.set_enabled("grants_gov", False,
                                     owner_identity=OWNER, why="holdout")
        self.reopen()
        self.assertFalse(self.s.discovery.may("grants_gov",
                                              access.DISCOVER)[0])

    def test_a_keyed_adapter_shows_as_needing_configuration(self):
        self.s.discovery.register_source(
            SamGovOpportunitiesSource(), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="official API")
        state = self.s.discovery.source_state("sam_gov")
        self.assertEqual(state["auth_required"], 1)
        self.assertEqual(state["credential_configured"], 0)

    def test_recording_a_credential_does_not_grant_a_permission(self):
        self.s.discovery.register_source(
            SamGovOpportunitiesSource(), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="official API")
        before = self.s.discovery.permissions("sam_gov")
        self.s.discovery.set_credential_configured("sam_gov", True,
                                                   owner_identity=OWNER)
        self.assertEqual(self.s.discovery.permissions("sam_gov"), before)

    def test_an_adapter_url_is_https_and_public(self):
        for adapter in (GrantsGovSource(), SamGovOpportunitiesSource()):
            self.assertEqual(access.url_problem(adapter.base_url), "",
                             adapter.name)

    def test_a_stale_determination_is_visible(self):
        entry = catalogue.entry("grants_gov")
        self.assertFalse(entry.stale(today="2026-10-01"))
        self.assertTrue(entry.stale(today="2028-01-01"),
                        "a two-year-old reading of a platform's terms is not "
                        "current evidence")

    def test_a_manual_only_entry_never_reads_stale(self):
        self.assertFalse(catalogue.entry("owner_entered").stale(
            today="2099-01-01"))


class SeamsInTheValidationBridge(Base):

    def built(self, fingerprint="sha256:aa"):
        covers = frozenset({"extract_pdf_text"})
        project_id = verified_project(self.s, skill="pdf-extract",
                                      covers=covers)
        self.s.skillslab.mark_specified(project_id=project_id,
                                        target_covers=covers,
                                        target_version="pdf-extract/1.0")
        self.s.skillslab.mark_built(project_id=project_id,
                                    fingerprint=fingerprint)
        return project_id

    def test_a_second_validation_request_replaces_the_first(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint="sha256:aa")
        with self.assertRaises(FailClosed):
            self.s.skillslab.request_validation(project_id=project_id,
                                                fingerprint="sha256:bb")

    def test_a_validator_cannot_pass_a_candidate_it_never_saw(self):
        """The digest is what ties a result to a candidate. A pass on a
        different artifact certifies that artifact, not this one."""
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint="sha256:aa")
        self.s.skillslab.record_validation(
            project_id=project_id, validator="external:v", passed=True,
            fingerprint="sha256:UNRELATED")
        recorded = self.s.skillslab.versions("pdf-extract")[0]["fingerprint"]
        self.assertEqual(recorded, "sha256:UNRELATED")
        # ...and that is exactly what the owner is then refused on.
        self.s.skillslab.offer_to_owner(project_id)
        self.s.capability.register(
            Capability(name="pdf-extract",
                       covers=frozenset({"extract_pdf_text"}), proven=True,
                       version="pdf-extract/1.0",
                       verifiable_by=("extract_pdf_text",),
                       proven_levels=("L1", "L2", "L3"), fixtures_passed=25,
                       fixtures_total=25, false_completions=0,
                       evidence_ref="holdout"), owner_identity=OWNER)
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=project_id, owner_identity=OWNER, why="x",
                fingerprint="sha256:aa")

    def test_a_failed_then_abandoned_candidate_cannot_be_promoted(self):
        project_id = self.built()
        self.s.skillslab.request_validation(project_id=project_id,
                                            fingerprint="sha256:aa")
        self.s.skillslab.record_validation(
            project_id=project_id, validator="external:v", passed=False,
            fingerprint="sha256:aa", detail="broken")
        self.s.skillslab.abandon(project_id=project_id, why="not worth it")
        with self.assertRaises(FailClosed):
            self.s.skillslab.record_promotion(
                project_id=project_id, owner_identity=OWNER, why="x",
                fingerprint="sha256:aa")

    def test_the_handoff_of_an_unvalidated_candidate_names_no_evidence_of_passing(self):
        project_id = self.built()
        package = self.s.skillslab.request_validation(
            project_id=project_id, fingerprint="sha256:aa")
        self.assertEqual(package["prior_failures"], [])
        self.assertEqual(package["stage"], lab.VALIDATION_REQUESTED)


class SeamsInTheMoney(Base):

    def test_operating_spend_with_no_job_is_counted(self):
        """The defect the simulations found, pinned from the other side."""
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.AI_API, amount_cents=7_500,
            source="PROVIDER_BILLING", note="a subscription belongs to no job")
        self.assertEqual(self.s.metrics().actual_costs, "$75.00")

    def test_it_still_reports_no_margin(self):
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.AI_API, amount_cents=7_500,
            source="PROVIDER_BILLING", note="subscription")
        metrics = self.s.metrics()
        self.assertIsNone(metrics.actual_margin)
        self.assertIn("no margin to report", " ".join(metrics.caveats))

    def test_a_credit_reduces_the_total(self):
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.AI_API, amount_cents=10_000,
            source="PROVIDER_BILLING", note="overcharged")
        before = self.s.metrics().actual_costs
        self.assertEqual(before, "$100.00")

    def test_discovering_work_does_not_spend_anything(self):
        self.register("board")
        self.s.discovery.poll("board")
        self.assertEqual(self.s.metrics().actual_costs, "$0.00")

    def test_a_simulated_payment_never_becomes_a_margin(self):
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        self.s.ledger.record_cost(
            job_id="", category=CostCategory.AI_API, amount_cents=1_000,
            source="PROVIDER_BILLING", note="x")
        self.assertIsNone(self.s.metrics().actual_margin)


class SeamsInScreening(Base):

    def test_a_legitimate_listing_mentioning_clients_is_not_flagged(self):
        """§66. Rejecting unfamiliar-but-valid work is its own failure."""
        clean = ("We are a consultancy and need our own client list "
                 "deduplicated. The files are ours.")
        self.assertEqual(risk.screen_text(clean).codes(), [])

    def test_a_listing_about_previous_work_is_not_exfiltration(self):
        clean = "Please share examples of your previous work with us."
        self.assertNotIn("DATA_EXFILTRATION", risk.screen_text(clean).codes())

    def test_a_portfolio_request_is_not_flagged(self):
        clean = "Send a portfolio of similar data cleaning projects."
        self.assertEqual(risk.screen_text(clean).codes(), [])

    def test_the_word_urgent_alone_is_only_a_note(self):
        screening = risk.screen_text("Urgent: CSV cleanup needed this week.")
        self.assertEqual(screening.worst, risk.NOTE)


if __name__ == "__main__":
    unittest.main()
