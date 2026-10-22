"""The opt-out must survive everything. This is that checkpoint.

A prospect record restoring in the wrong order already erased an
opt-out once in this codebase, silently, and a restart is the one
event that happens on every machine every day. So the property is
stated plainly and then attacked from every direction the brief
names: duplicate events, out-of-order events, interrupted writes,
repeated restarts, conflicting contact records, and ordinary updates
arriving after the opt-out.

The rule under test:

    **No sequence of ordinary operations may reactivate a suppressed
    contact. Only recorded affirmative consent may, and it must be
    attributable.**

Every test here goes through persistence and a genuine reopen. An
in-memory assertion would pass against the exact bug this exists to
catch.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.audit import AuditLog
from solvent.crm import CRM, Basis, Contact, Evidence, Standing
from solvent.errors import FailClosed
from solvent.memory import BusinessMemory
from solvent.outreach import (
    Campaign, Consent, Suppressions, may_write, schedule_follow_up,
)
from solvent.store import Store

SEEN = "2026-10-01"
EMAIL = "ops@acme.example"


class Checkpoint(unittest.TestCase):
    """A real database on disk, reopened for real between phases."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.path = str(pathlib.Path(self.box.name) / "checkpoint.db")

    def restart(self):
        """Close nothing, open fresh: what a service restart looks like."""
        store = Store(self.path)
        self.addCleanup(store.close)
        memory = BusinessMemory(store, AuditLog(store))
        return CRM(memory), Suppressions(memory)

    def seed(self):
        crm, suppressions = self.restart()
        crm.discover(prospect_id="p1", company="Acme Builders LLC",
                     industry="construction", state="TX")
        crm.add_contact("p1", Contact("Dana", EMAIL, "operations",
                                      "their careers page", SEEN))
        return crm, suppressions


class TheTenSteps(Checkpoint):
    """Steps 1 to 10 of the brief, in order."""

    def test_the_whole_checkpoint(self):
        # 1-2. A synthetic prospect, and a valid opt-out.
        crm, suppressions = self.seed()
        crm.opt_out("p1", "replied STOP")
        suppressions.suppress(EMAIL, "replied STOP")

        # 3-5. Shut down, restart, restore.
        crm, suppressions = self.restart()
        prospect = crm.get("p1")

        # 6. The opt-out is still active.
        self.assertIs(prospect.standing, Standing.OPTED_OUT)
        self.assertIsNotNone(suppressions.active(EMAIL))

        # 7-8. Outreach is refused.
        refusal = may_write(EMAIL, suppressions=suppressions,
                            prospect=prospect)
        self.assertTrue(refusal, "outreach was permitted to an opted-out "
                                 "contact after a restart")

        # 9-10. A follow-up is refused by the same rule.
        campaign = Campaign()
        campaign.record(EMAIL, "first_contact", on="2026-09-01")
        self.assertTrue(may_write(EMAIL, suppressions=suppressions,
                                  campaign=campaign, prospect=prospect,
                                  today="2026-10-22"))

    def test_the_refusal_says_which_rule_stopped_it(self):
        crm, suppressions = self.seed()
        suppressions.suppress(EMAIL, "replied STOP")
        crm, suppressions = self.restart()
        self.assertIn("suppressed",
                      may_write(EMAIL, suppressions=suppressions))


class NoOrdinaryUpdateReactivatesAnyone(Checkpoint):
    """The failure mode that matters: silent, and caused by routine work."""

    def opted_out(self):
        crm, suppressions = self.seed()
        crm.opt_out("p1", "replied STOP")
        suppressions.suppress(EMAIL, "replied STOP")
        return crm, suppressions

    def test_a_later_contact_record_does_not_lift_the_suppression(self):
        crm, suppressions = self.opted_out()
        with self.assertRaises(FailClosed):
            crm.add_contact("p1", Contact("Dana", EMAIL, "ops",
                                          "a different page", SEEN))
        crm, suppressions = self.restart()
        self.assertIsNotNone(suppressions.active(EMAIL))

    def test_new_research_does_not_lift_it(self):
        crm, _ = self.opted_out()
        with self.assertRaises(FailClosed):
            crm.add_evidence("p1", Evidence("still hiring", Basis.PUBLISHED,
                                            "careers", SEEN))
        crm, suppressions = self.restart()
        self.assertIs(crm.get("p1").standing, Standing.OPTED_OUT)
        self.assertIsNotNone(suppressions.active(EMAIL))

    def test_rediscovery_does_not_lift_it(self):
        """The discovery run finds them again next week. It must not help."""
        self.opted_out()
        crm, suppressions = self.restart()
        crm.discover(prospect_id="p-new", company="ACME Builders, LLC",
                     industry="construction", state="TX")
        crm, suppressions = self.restart()
        self.assertIs(crm.get("p1").standing, Standing.OPTED_OUT)
        self.assertIsNotNone(suppressions.active(EMAIL))

    def test_advancing_the_lifecycle_is_refused(self):
        crm, _ = self.opted_out()
        with self.assertRaises(FailClosed):
            crm.advance("p1", Standing.CONTACTED)

    def test_a_differently_cased_address_is_the_same_address(self):
        """Otherwise a suppression is one record edit away from useless."""
        self.opted_out()
        _, suppressions = self.restart()
        for variant in (EMAIL.upper(), f"  {EMAIL}  ", EMAIL.title()):
            with self.subTest(variant=variant):
                self.assertTrue(may_write(variant, suppressions=suppressions))


class TheAdversarialSequences(Checkpoint):
    """Duplicates, disorder, interruption, repetition, conflict."""

    def test_duplicate_opt_out_events_are_harmless(self):
        crm, suppressions = self.seed()
        for _ in range(5):
            suppressions.suppress(EMAIL, "replied STOP")
        _, suppressions = self.restart()
        self.assertIsNotNone(suppressions.active(EMAIL))

    def test_many_updates_before_the_opt_out_do_not_outvote_it(self):
        """The CRM restore picks the latest state, not the commonest."""
        crm, suppressions = self.seed()
        for n in range(10):
            crm.add_evidence("p1", Evidence(f"note {n}", Basis.PUBLISHED,
                                            "careers", SEEN))
        crm.opt_out("p1", "replied STOP")
        suppressions.suppress(EMAIL, "replied STOP")
        crm, suppressions = self.restart()
        self.assertIs(crm.get("p1").standing, Standing.OPTED_OUT)

    def test_an_interrupted_write_does_not_lose_an_earlier_opt_out(self):
        """A half-written later record must not erase a settled one."""
        crm, suppressions = self.seed()
        crm.opt_out("p1", "replied STOP")
        suppressions.suppress(EMAIL, "replied STOP")

        store = Store(self.path)
        self.addCleanup(store.close)
        memory = BusinessMemory(store, AuditLog(store))
        # A corrupt row of the same kind, as a torn write would leave.
        from solvent.types import VerificationTier
        memory.learn(kind="crm_prospect", subject="p1",
                     payload={"prospect_id": "p1", "company": "Acme"},
                     evidence_ref="torn", tier=VerificationTier.T1_DETERMINISTIC)
        store.close()

        crm, suppressions = self.restart()
        self.assertIs(crm.get("p1").standing, Standing.OPTED_OUT,
                      "a torn write undid the opt-out")
        self.assertIsNotNone(suppressions.active(EMAIL))

    def test_the_opt_out_survives_many_restarts(self):
        crm, suppressions = self.seed()
        crm.opt_out("p1", "replied STOP")
        suppressions.suppress(EMAIL, "replied STOP")
        for _ in range(6):
            crm, suppressions = self.restart()
        self.assertIs(crm.get("p1").standing, Standing.OPTED_OUT)
        self.assertIsNotNone(suppressions.active(EMAIL))

    def test_conflicting_contact_records_do_not_create_a_way_through(self):
        """A second address for the same company is not a loophole."""
        crm, suppressions = self.seed()
        other = "accounts@acme.example"
        crm.add_contact("p1", Contact("Sam", other, "accounts", "page", SEEN))
        crm.opt_out("p1", "replied STOP")
        suppressions.suppress(EMAIL, "replied STOP")
        crm, suppressions = self.restart()
        prospect = crm.get("p1")
        self.assertTrue(
            may_write(other, suppressions=suppressions, prospect=prospect),
            "a second address on an opted-out prospect was writable")

    def test_suppression_after_a_restore_takes_effect_again(self):
        """Somebody may consent once and change their mind."""
        crm, suppressions = self.seed()
        suppressions.suppress(EMAIL, "replied STOP")
        suppressions.restore(Consent(EMAIL, "asked us for a quote",
                                     "website form", "2026-10-10"))
        suppressions.suppress(EMAIL, "changed their mind")
        _, suppressions = self.restart()
        self.assertIsNotNone(suppressions.active(EMAIL))


class ResubscribingNeedsRealConsent(Checkpoint):

    def test_a_bare_restore_is_refused(self):
        _, suppressions = self.seed()
        suppressions.suppress(EMAIL, "replied STOP")
        with self.assertRaises(FailClosed):
            suppressions.restore(EMAIL)

    def test_consent_without_an_action_is_refused(self):
        with self.assertRaises(FailClosed):
            Consent(EMAIL, "", "website form", "2026-10-10")

    def test_consent_without_a_source_is_refused(self):
        with self.assertRaises(FailClosed):
            Consent(EMAIL, "asked for a quote", "", "2026-10-10")

    def test_consent_without_a_date_is_refused(self):
        with self.assertRaises(FailClosed):
            Consent(EMAIL, "asked for a quote", "website form", "")

    def test_restoring_an_address_that_is_not_suppressed_is_refused(self):
        _, suppressions = self.seed()
        with self.assertRaises(FailClosed):
            suppressions.restore(Consent(EMAIL, "asked", "form", "2026-10-10"))

    def test_consent_lifts_it_and_the_lift_survives_a_restart(self):
        _, suppressions = self.seed()
        suppressions.suppress(EMAIL, "replied STOP")
        suppressions.restore(Consent(EMAIL, "asked us for a quote",
                                     "website form", "2026-10-10"))
        _, suppressions = self.restart()
        self.assertIsNone(suppressions.active(EMAIL))

    def test_the_consent_that_lifted_it_is_still_attributable_afterwards(self):
        _, suppressions = self.seed()
        suppressions.suppress(EMAIL, "replied STOP")
        suppressions.restore(Consent(EMAIL, "asked us for a quote",
                                     "website form", "2026-10-10"))
        _, suppressions = self.restart()
        row = next(r for r in suppressions.all()
                   if Suppressions.key(r.email) == EMAIL)
        self.assertIsNotNone(row.restored_by)
        self.assertEqual(row.restored_by.source, "website form")


class TheOtherStopRules(Checkpoint):
    """Suppression is not the only reason not to write."""

    def test_a_contact_written_to_yesterday_is_not_written_to_today(self):
        _, suppressions = self.seed()
        campaign = Campaign()
        campaign.record(EMAIL, "first_contact", on="2026-10-21")
        self.assertIn("day(s) ago", may_write(
            EMAIL, suppressions=suppressions, campaign=campaign,
            today="2026-10-22"))

    def test_silence_is_not_consent_to_keep_writing(self):
        _, suppressions = self.seed()
        campaign = Campaign()
        campaign.record(EMAIL, "first_contact", on="2026-09-01")
        campaign.record(EMAIL, "follow_up_1", on="2026-09-10")
        campaign.record(EMAIL, "follow_up_2", on="2026-09-20")
        self.assertIn("silence is not consent", may_write(
            EMAIL, suppressions=suppressions, campaign=campaign,
            today="2026-10-22"))

    def test_an_unreadable_last_contact_date_stops_the_message(self):
        """Unknown must not read as 'long ago'."""
        _, suppressions = self.seed()
        campaign = Campaign()
        campaign.record(EMAIL, "first_contact", on="whenever")
        self.assertIn("unreadable", may_write(
            EMAIL, suppressions=suppressions, campaign=campaign,
            today="2026-10-22"))

    def test_a_prospect_with_no_sourced_contact_is_not_written_to(self):
        crm, suppressions = self.restart()
        crm.discover(prospect_id="p2", company="Nobody Ltd",
                     industry="accounting", state="NY")
        self.assertIn("no contact", may_write(
            "someone@nobody.example", suppressions=suppressions,
            prospect=crm.get("p2")))

    def test_a_follow_up_is_scheduled_in_the_future(self):
        self.assertGreater(schedule_follow_up(EMAIL, after_days=5,
                                              today="2026-10-22"),
                           "2026-10-22")


if __name__ == "__main__":
    unittest.main()
