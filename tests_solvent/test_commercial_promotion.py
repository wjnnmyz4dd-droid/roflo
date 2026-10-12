"""The owner's clearance of four capabilities for client work, and its limits.

A promotion is the one decision in this system that Solvent may never make for
itself, so most of this file is about the ways it is refused. What the owner
authorised is narrow and specific: four capabilities, on their recorded
certification and black-box evidence, with one check explicitly withheld.

Three properties carry the whole thing:

**LIMITED to APPROVED is a new decision, not an edit.** The LIMITED row stays
on the record. "When did this become sellable, and on what evidence" has to
stay answerable, and a table that overwrote the answer could not answer it.

**The promotion claims only what the evidence reaches.** ``number_format``
cannot be certified by any battery, so it is not in any promotion's scope, so a
job resting on it is refused — after the promotion exactly as before it.
Fourteen of fifteen, enforced rather than footnoted.

**Clearing a skill for client work clears nothing else.** It is not permission
to accept a job, send a proposal, contact a client, spend money, or leave
simulation mode. Those are separate authorities and this file asserts each one
is untouched.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import checks
from solvent.capability import MIN_FIXTURES, MIN_LEVELS, promotion_verdict
from solvent.errors import FailClosed
from solvent.harness import (
    CAPABILITIES, CSV_PROMOTION, OWNER, Solvent, UNCERTIFIABLE,
    certifiable_scope, ensure_verifier_certified, owner_promotions,
    promote_capability, provision_capability, provision_development_only,
)

#: The four the owner cleared in this authorisation. csv-cleanup was already
#: cleared and its status is preserved, which is asserted rather than assumed.
AUTHORISED = ("report-builder", "spreadsheet-automation", "data-entry",
              "document-processing")

WHY = ("owner authorisation: commercial promotion of the four remaining "
       "capabilities, subject to verification of their certification records")


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = pathlib.Path(self.dir.name) / "owner.db"
        self.s = Solvent(str(self.path))
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def as_the_owner_had_it(self):
        """csv-cleanup cleared, the other four certified and LIMITED.

        csv-cleanup goes through the full provisioning path, decision included,
        because that is what `solvent setup capability` left on the owner's
        database before this authorisation. Its APPROVED row therefore carries
        its *own* reason, which is what makes "preserve its status" testable
        rather than a matter of looking at it.
        """
        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        for name in AUTHORISED:
            ensure_verifier_certified(self.s, name)
            provision_development_only(
                self.s, name, certifiable_scope(name), owner_identity=OWNER,
                why="certified and not yet cleared for client delivery")

    def promote_all(self):
        return {name: promote_capability(self.s, name, owner_identity=OWNER,
                                         why=WHY)
                for name in AUTHORISED}

    def decisions(self, name):
        return [r["decision"] for r in self.s.capability.proposals(name)
                if r["kind"] == "DECIDED"]


class TheEvidenceIsCheckedBeforeAnythingIsRecorded(Base):

    def test_every_promotion_record_clears_the_evidence_floor(self):
        for name, capability in owner_promotions().items():
            with self.subTest(name=name):
                verdict, why = promotion_verdict(capability)
                self.assertTrue(verdict, why)
                self.assertGreaterEqual(capability.fixtures_total, MIN_FIXTURES)
                self.assertGreaterEqual(len(capability.proven_levels), MIN_LEVELS)
                self.assertEqual(capability.false_completions, 0)
                self.assertTrue(capability.verifiable_by)
                self.assertTrue(capability.version)

    def test_the_certification_is_re_run_against_the_current_code(self):
        from solvent import verifiercert

        self.as_the_owner_had_it()
        records = self.promote_all()
        for name, record in records.items():
            with self.subTest(name=name):
                self.assertEqual(record["state"], "CERTIFIED")
                self.assertEqual(
                    record["fingerprint"],
                    verifiercert.implementation_fingerprint(
                        CAPABILITIES[name].verify),
                    "the promotion rests on a certification of different code")

    def test_a_promotion_claiming_more_than_the_evidence_is_refused(self):
        from solvent.capability import Capability
        from solvent import harness

        name = "spreadsheet-automation"
        everything = frozenset(checks.checks_for(name))
        wider = Capability(
            name=name, covers=everything, proven=True,
            version=CAPABILITIES[name].version,
            verifiable_by=tuple(sorted(everything)),
            proven_levels=("L1", "L2", "L3"), fixtures_passed=17,
            fixtures_total=17, false_completions=0, evidence_ref="test")
        original = harness._owner_promoted
        harness._owner_promoted = lambda n: wider if n == name else original(n)
        self.addCleanup(setattr, harness, "_owner_promoted", original)

        with self.assertRaises(FailClosed) as caught:
            promote_capability(self.s, name, owner_identity=OWNER, why=WHY)
        message = str(caught.exception)
        self.assertIn("number_format", message)
        self.assertIn("does not support this scope", message)
        self.assertIn("Nothing was recorded", message)
        self.assertEqual(self.decisions(name), [],
                         "a refused promotion recorded a decision anyway")

    def test_a_capability_with_a_false_completion_is_refused(self):
        from solvent.capability import Capability
        from solvent import harness

        name = "data-entry"
        covers = certifiable_scope(name)
        tainted = Capability(
            name=name, covers=covers, proven=True,
            version=CAPABILITIES[name].version,
            verifiable_by=tuple(sorted(covers)),
            proven_levels=("L1", "L2", "L3"), fixtures_passed=17,
            fixtures_total=17, false_completions=1, evidence_ref="test")
        original = harness._owner_promoted
        harness._owner_promoted = lambda n: tainted if n == name else original(n)
        self.addCleanup(setattr, harness, "_owner_promoted", original)

        with self.assertRaises(FailClosed) as caught:
            promote_capability(self.s, name, owner_identity=OWNER, why=WHY)
        message = str(caught.exception)
        self.assertIn("false completion", message)
        self.assertIn("Nothing was recorded", message)

    def test_a_capability_this_build_does_not_have_is_refused(self):
        with self.assertRaises(FailClosed) as caught:
            promote_capability(self.s, "render-pdf", owner_identity=OWNER)
        self.assertIn("not a capability this build has", str(caught.exception))


class OnlyTheOwnerDecides(Base):

    def test_a_component_cannot_promote_a_capability(self):
        self.as_the_owner_had_it()
        for identity in ("skillslab", "worker", "qc", "capability",
                         "owner-ish", ""):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed) as caught:
                    promote_capability(self.s, "data-entry",
                                       owner_identity=identity, why=WHY)
                self.assertIn("only a registered owner",
                              str(caught.exception))

    def test_an_unregistered_owner_name_is_not_an_owner(self):
        self.as_the_owner_had_it()
        with self.assertRaises(FailClosed) as caught:
            promote_capability(self.s, "data-entry",
                               owner_identity="owner:somebody-else", why=WHY)
        self.assertIn("only a registered owner", str(caught.exception))

    def test_a_component_asking_about_an_undecided_capability_writes_nothing(self):
        """The refusal lands before the proposal step, so no row is attributed.

        With a decision already on the record the proposal step is skipped, so
        that case proves nothing. Undecided is where an unauthorised attempt
        could leave something behind: a PROPOSED row carrying whatever identity
        asked for it, in a table that cannot be edited afterwards.
        """
        ensure_verifier_certified(self.s, "data-entry")
        with self.assertRaises(FailClosed) as caught:
            promote_capability(self.s, "data-entry",
                               owner_identity="skillslab", why=WHY)
        self.assertIn("only a registered owner", str(caught.exception))
        self.assertEqual(self.s.capability.proposals("data-entry"), [],
                         "an unauthorised attempt filed a question")

    def test_the_refused_attempt_left_no_trace_at_all(self):
        self.as_the_owner_had_it()
        before = self.s.capability.proposals("data-entry")
        with self.assertRaises(FailClosed):
            promote_capability(self.s, "data-entry",
                               owner_identity="skillslab", why=WHY)
        self.assertEqual(self.s.capability.proposals("data-entry"), before,
                         "a refused attempt left a row on the record")
        self.assertEqual(self.decisions("data-entry"), ["LIMITED"])
        permitted, why = self.s.capability.may_deploy("data-entry")
        self.assertFalse(permitted, why)

    def test_the_registry_refuses_a_component_even_without_that_check(self):
        """The early refusal is a courtesy; the registry is the control.

        Reading :func:`promote_capability` and finding the identity check
        there proves nothing about where authority lives. So ask the registry
        directly, the way the promotion does: if the early check were deleted
        tomorrow, this is what would still refuse.
        """
        self.as_the_owner_had_it()
        with self.assertRaises(FailClosed) as caught:
            self.s.capability.decide(
                name="data-entry", decision=self.s.capability.APPROVED,
                owner_identity="skillslab", why=WHY, scope="")
        self.assertIn("only a registered owner", str(caught.exception))
        self.assertEqual(self.decisions("data-entry"), ["LIMITED"])


class LimitedBecomesApprovedWithoutLosingTheRecord(Base):

    def setUp(self):
        super().setUp()
        self.as_the_owner_had_it()
        for name in AUTHORISED:
            permitted, _ = self.s.capability.may_deploy(name)
            self.assertFalse(permitted, f"{name} was deliverable before")
        self.promote_all()

    def test_the_registry_confirms_each_one_is_approved_and_proven(self):
        for name in AUTHORISED:
            with self.subTest(name=name):
                decision, _ = self.s.capability.development_decision(name)
                self.assertEqual(decision, "APPROVED")
                registered = [c for c in self.s.capability.capabilities()
                              if c.name == name]
                self.assertTrue(registered, "nothing was registered as proven")
                self.assertTrue(registered[0].proven)
                permitted, why = self.s.capability.may_deploy(name)
                self.assertTrue(permitted, why)

    def test_the_limited_row_is_still_on_the_record(self):
        for name in AUTHORISED:
            with self.subTest(name=name):
                self.assertEqual(self.decisions(name), ["LIMITED", "APPROVED"],
                                 "the earlier answer was overwritten rather "
                                 "than superseded")

    def test_the_approval_is_in_the_audit_journal_with_the_owners_reason(self):
        """Four decisions, under the owner's identity, carrying their reason.

        Counted by the reason this authorisation supplied rather than by
        "every APPROVED row", because csv-cleanup's own earlier approval is
        also in this journal and counting it would hide a promotion that
        never happened behind a number that still looked right.
        """
        rows = self.s.store.raw_readonly(
            "SELECT * FROM audit_log WHERE event = 'capability.decided' "
            "ORDER BY seq")
        approvals = [r for r in rows if "APPROVED" in str(r["decision"])
                     and WHY in str(r["why"])]
        self.assertEqual(len(approvals), len(AUTHORISED))
        for row in approvals:
            self.assertEqual(row["initiator"], OWNER)
        self.assertEqual(
            len([r for r in rows if "APPROVED" in str(r["decision"])]),
            len(AUTHORISED) + 1,
            "csv-cleanup's own approval is missing from the journal")

    def test_csv_cleanup_is_untouched(self):
        permitted, why = self.s.capability.may_deploy("csv-cleanup")
        self.assertTrue(permitted, why)
        registered = [c for c in self.s.capability.capabilities()
                      if c.name == "csv-cleanup"]
        self.assertEqual(len(registered), 1)
        self.assertEqual(set(registered[0].covers), set(CSV_PROMOTION.covers))

    def test_csv_cleanups_own_approval_was_not_restamped(self):
        """Its existing decision keeps its existing date and reason.

        A promotion run that re-decided every capability would overwrite
        nothing — the table is append-only — but it would append a second
        APPROVED row to csv-cleanup carrying *this* authorisation's reason, and
        the record would then say the owner cleared csv-cleanup today on the
        strength of a message about four other capabilities. It did not.
        """
        rows = [r for r in self.s.capability.proposals("csv-cleanup")
                if r["kind"] == "DECIDED"]
        self.assertEqual(len(rows), 1, "csv-cleanup was decided a second time")
        self.assertEqual(rows[0]["decision"], "APPROVED")
        self.assertNotIn("owner authorisation", str(rows[0]["why"]),
                         "an existing approval was restamped with this one's "
                         "reason")

    def test_promoting_twice_changes_nothing(self):
        """An owner may reasonably run it again, and a restart must not need it."""
        before = {name: self.decisions(name) for name in AUTHORISED}
        self.promote_all()
        for name in AUTHORISED:
            with self.subTest(name=name):
                self.assertEqual(self.decisions(name), before[name],
                                 "a second run appended a second APPROVED")


class TheWithheldCheckStaysWithheld(Base):

    def setUp(self):
        super().setUp()
        self.as_the_owner_had_it()
        self.promote_all()

    def test_number_format_is_not_in_the_promoted_scope(self):
        registered = [c for c in self.s.capability.capabilities()
                      if c.name == "spreadsheet-automation"][0]
        self.assertNotIn("number_format", registered.covers)
        self.assertEqual(len(registered.covers),
                         len(checks.checks_for("spreadsheet-automation")) - 1)

    def test_delivery_on_number_format_is_refused_after_the_promotion(self):
        permitted, why = self.s.capability.may_authorize_delivery(
            verifier_ref="solvent.sheetverify.run",
            capability_version="spreadsheet-automation/1.0",
            check="number_format")
        self.assertFalse(permitted, "a promotion certified an uncertified check")
        self.assertIn("not certified to decide", why)

    def test_a_certified_check_of_the_same_capability_is_permitted(self):
        """The refusal above is about that check, not about the capability."""
        permitted, why = self.s.capability.may_authorize_delivery(
            verifier_ref="solvent.sheetverify.run",
            capability_version="spreadsheet-automation/1.0",
            check="compute_column")
        self.assertTrue(permitted, why)

    def test_the_exemption_is_still_recorded_with_its_reason(self):
        self.assertIn("number_format", UNCERTIFIABLE["spreadsheet-automation"])
        reason = UNCERTIFIABLE["spreadsheet-automation"]["number_format"]
        self.assertIn("no value to recompute", reason)


class ApprovedCapabilitiesBecomeEligibleForClientWork(Base):
    """The point of the promotion, tested through the real pipeline."""

    def requirement(self, rid, check, params=None):
        from solvent.types import Criticality, Requirement, RequirementSource

        return Requirement(
            id=rid, text=check,
            source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
            acceptance=check, check=check, params=params or {},
            criticality=Criticality.MANDATORY)

    def arrange_delivery_policy(self):
        """One approved destination, simulation only — the owner's posture.

        Set here rather than letting ``run_csv_job`` do it, because that
        fixture path also provisions the capability for the caller. Convenient
        for a suite measuring a worker; fatal for this one, where whether the
        capability is cleared *is the thing under test*. With
        ``configure_fixture_policy=False`` the job asks the database, and the
        database holds only what the owner's promotion put there.
        """
        self.s.policy.amend({
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
        }, OWNER, "promotion suite: one destination, simulation only")

    def run_job(self, name, source, requirements):
        from solvent.harness import run_csv_job

        return run_csv_job(
            source=str(source), requirements=requirements,
            workdir=self.dir.name, solvent=self.s, client_id="client:promo",
            title=f"promoted-{name}", capability=name,
            configure_fixture_policy=False)

    def test_a_promoted_capability_delivers_on_the_records_own_authority(self):
        """Nothing clears the capability for this job but the owner's promotion.

        Before it, this job could not be delivered at all: the scenario runs
        that exercise these capabilities each had to clear their own inside a
        throwaway Solvent to measure anything. That is what changed.
        """
        self.as_the_owner_had_it()
        self.promote_all()
        self.arrange_delivery_policy()
        source = pathlib.Path(self.dir.name) / "records.csv"
        source.write_text(
            "Ref,Client Name,Invoiced,Due\n"
            "A-1001,  Acme   Corp ,1250.00,2026-03-01\n"
            "A-1002,,480.50,2026-03-15\n", encoding="utf-8")
        report = self.run_job("data-entry", source, [
            self.requirement("R1", "map_fields", {"mapping": {
                "Reference": "Ref", "Client": "Client Name",
                "Amount": "Invoiced", "Due Date": "Due"}}),
            self.requirement("R2", "normalise_whitespace",
                             {"fields": ["Client"]}),
            self.requirement("R3", "require_field",
                             {"fields": ["Reference", "Client"]}),
            self.requirement("R4", "records_reconcile", {}),
            self.requirement("R5", "exceptions_justified", {})])
        self.assertTrue(report.delivered, report.escalated[:400])
        delivered = pathlib.Path(
            self.s.orchestrator.current_deliverable(report.job_id).path)
        self.assertIn("Acme Corp", delivered.read_text(encoding="utf-8"))

    def held_job(self, name):
        """Run a job the capability is not cleared for, and return the report.

        The refusal arrives as an escalation rather than an exception, which is
        the behaviour worth having: the work was done and verified, and a human
        is told the capability is not cleared to send it. Nothing reaches the
        client either way, and the assertion below is that nothing did.
        """
        self.arrange_delivery_policy()
        source = pathlib.Path(self.dir.name) / f"held-{name}.csv"
        source.write_text("Ref,Client Name\nA-1001,Acme Corp\n",
                          encoding="utf-8")
        report = self.run_job(name, source, [
            self.requirement("R1", "map_fields",
                             {"mapping": {"Reference": "Ref"}})])
        self.assertFalse(report.delivered,
                         "an uncleared capability reached a client")
        self.assertIn("delivery gate refused", report.escalated)
        self.assertEqual(
            self.s.store.raw_readonly(
                "SELECT * FROM submission_attempts"), [],
            "an uncleared capability produced a submission attempt")
        return report.escalated

    def test_a_certified_but_unpromoted_capability_cannot_deliver(self):
        """The gate is the owner's decision, not the certification."""
        ensure_verifier_certified(self.s, "data-entry")
        escalation = self.held_job("data-entry")
        self.assertIn("has not been proposed", escalation)

    def test_a_limited_capability_cannot_deliver(self):
        self.as_the_owner_had_it()
        escalation = self.held_job("data-entry")
        self.assertIn("development and testing only", escalation)


class ClearingASkillClearsNothingElse(Base):
    """Every restriction the owner named, asserted after the promotion."""

    def setUp(self):
        super().setUp()
        self.as_the_owner_had_it()
        self.before = {
            "operating_mode": self.s.policy.get("governance", "operating_mode",
                                                default=None),
            "simulation_only": self.s.policy.get("governance",
                                                 "simulation_only",
                                                 default=None),
            "egress": self.s.policy.get("egress", "allowlist", default=None),
            "max_spend": self.s.policy.get("financial",
                                           "max_spend_cents_per_job",
                                           default=None),
        }
        self.promote_all()

    def test_no_policy_value_changed(self):
        for key, was in self.before.items():
            with self.subTest(key=key):
                now = {
                    "operating_mode": self.s.policy.get(
                        "governance", "operating_mode", default=None),
                    "simulation_only": self.s.policy.get(
                        "governance", "simulation_only", default=None),
                    "egress": self.s.policy.get("egress", "allowlist",
                                                default=None),
                    "max_spend": self.s.policy.get(
                        "financial", "max_spend_cents_per_job", default=None),
                }[key]
                self.assertEqual(now, was,
                                 "promoting a skill changed a policy value")

    def test_no_job_was_accepted_and_no_application_sent(self):
        for table in ("jobs", "applications", "action_requests",
                      "ledger_entries", "payments", "payment_events",
                      "submission_attempts", "opportunities",
                      "governor_decisions", "notifications"):
            with self.subTest(table=table):
                rows = self.s.store.raw_readonly(f"SELECT * FROM {table}")
                self.assertEqual(list(rows), [],
                                 f"promoting a skill wrote to {table}")

    def test_the_action_gate_and_governor_are_unchanged(self):
        """Promotion is recorded by the capability registry and nobody else."""
        events = {str(r["event"]) for r in self.s.store.raw_readonly(
            "SELECT event FROM audit_log")}
        for forbidden in ("gate.", "governor.", "ledger.", "payment.",
                          "submission."):
            with self.subTest(prefix=forbidden):
                self.assertEqual(
                    [e for e in events if e.startswith(forbidden)], [],
                    f"promoting a skill produced a {forbidden} event")

    def test_the_capability_registry_is_the_only_authority_that_wrote(self):
        """A promotion is one authority's decision, recorded in one place.

        If a second authority appears here, something took the promotion as
        licence to act — which is exactly the coupling the owner's restriction
        list forbids.
        """
        authorities = {str(r["authority"]) for r in self.s.store.raw_readonly(
            "SELECT authority FROM audit_log")}
        self.assertEqual(authorities, {"capability"},
                         f"an unexpected authority wrote during a promotion: "
                         f"{sorted(authorities)}")


if __name__ == "__main__":
    unittest.main()
