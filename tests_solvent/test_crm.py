"""Prospect records: what is known, what is guessed, and what survives.

The property under test throughout is that belief is structural. A
published fact, a client assertion and a DeskPilot inference are three
different things, and a CRM that stores them in one column produces a
pipeline of invented problems that reads exactly like a pipeline of
real ones.

The restart tests are not decoration. ``learn`` appends, so current
state is the newest row, and getting that wrong restores a prospect as
freshly discovered with nothing known about them -- which is both
silent and total.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.audit import AuditLog
from solvent.crm import (
    CRM, Basis, Contact, Evidence, INDUSTRIES, Prospect, Standing,
    fingerprint,
)
from solvent.errors import FailClosed
from solvent.memory import BusinessMemory
from solvent.store import Store

SEEN = "2026-10-01"


def published(claim="Advertised for a data-entry clerk twice this year"):
    return Evidence(claim, Basis.PUBLISHED, "careers page", SEEN)


def inferred(claim="Probably drowning in manual data entry"):
    return Evidence(claim, Basis.INFERRED, "reasoning: two adverts", SEEN)


def contact(email="ops@example.com"):
    return Contact("Dana", email, "operations", "their careers page", SEEN)


class ARecordSaysHowMuchItIsBelieved(unittest.TestCase):

    def setUp(self):
        self.crm = CRM()
        self.crm.discover(prospect_id="p1", company="Acme Builders LLC",
                          industry="construction", state="TX")

    def test_an_inference_is_not_a_verified_need(self):
        self.crm.add_evidence("p1", inferred())
        self.assertFalse(self.crm.get("p1").verified_need)

    def test_published_evidence_is_a_verified_need(self):
        self.crm.add_evidence("p1", published())
        self.assertTrue(self.crm.get("p1").verified_need)

    def test_a_client_assertion_is_neither(self):
        """Often true. Never evidence of itself."""
        self.crm.add_evidence("p1", Evidence(
            "we waste hours on spreadsheets", Basis.CLIENT_STATED,
            "their reply", SEEN))
        self.assertFalse(self.crm.get("p1").verified_need)

    def test_hypotheses_are_counted_separately(self):
        self.crm.add_evidence("p1", published())
        self.crm.add_evidence("p1", inferred())
        self.assertEqual(len(self.crm.get("p1").hypotheses), 1)
        self.assertEqual(self.crm.get("p1").summary()["published_evidence"], 1)

    def test_evidence_without_a_source_is_refused(self):
        with self.assertRaises(FailClosed) as caught:
            Evidence("they need help", Basis.INFERRED, "", SEEN)
        self.assertIn("invention", str(caught.exception))

    def test_evidence_without_a_date_is_refused(self):
        with self.assertRaises(FailClosed):
            Evidence("claim", Basis.PUBLISHED, "source", "")


class AContactNeedsAProvenance(unittest.TestCase):

    def test_a_contact_with_no_source_is_refused(self):
        with self.assertRaises(FailClosed) as caught:
            Contact("Dana", "d@x.com", "ops", "", SEEN)
        self.assertIn("origin", str(caught.exception))

    def test_a_thing_that_is_not_an_address_is_refused(self):
        with self.assertRaises(FailClosed):
            Contact("Dana", "not-an-address", "ops", "a page", SEEN)

    def test_the_same_address_is_not_held_twice(self):
        crm = CRM()
        crm.discover(prospect_id="p", company="Acme", industry="construction",
                     state="TX")
        crm.add_contact("p", contact())
        crm.add_contact("p", contact("OPS@EXAMPLE.COM"))
        self.assertEqual(len(crm.get("p").contacts), 1)


class TheTargetMarketIsEnforced(unittest.TestCase):

    def test_an_industry_outside_the_five_is_refused(self):
        with self.assertRaises(FailClosed):
            Prospect("p", "Acme", "aerospace", "TX")

    def test_each_of_the_five_is_accepted(self):
        for industry in INDUSTRIES:
            with self.subTest(industry=industry):
                self.assertTrue(Prospect("p", "Acme", industry, "TX"))

    def test_a_region_that_is_not_a_us_state_is_refused(self):
        for bad in ("Texas", "", "USA", "tx1"):
            with self.subTest(state=bad):
                with self.assertRaises(FailClosed):
                    Prospect("p", "Acme", "construction", bad)

    def test_a_prospect_with_no_name_is_refused(self):
        with self.assertRaises(FailClosed):
            Prospect("p", "   ", "construction", "TX")


class TheSameCompanyIsNotContactedTwice(unittest.TestCase):

    def test_legal_suffixes_and_case_do_not_make_a_new_company(self):
        self.assertEqual(fingerprint("Acme Builders, LLC", "TX"),
                         fingerprint("ACME Builders LLC", "TX"))

    def test_a_different_state_is_a_different_company(self):
        self.assertNotEqual(fingerprint("Acme Builders", "TX"),
                            fingerprint("Acme Builders", "CA"))

    def test_rediscovery_returns_the_existing_record(self):
        crm = CRM()
        first = crm.discover(prospect_id="p1", company="Acme Builders, LLC",
                             industry="construction", state="TX")
        again = crm.discover(prospect_id="p2", company="ACME Builders LLC",
                             industry="construction", state="TX")
        self.assertEqual(first.prospect_id, again.prospect_id)
        self.assertEqual(len(crm.all()), 1)

    def test_rediscovery_does_not_raise(self):
        """A discovery run that cannot be re-run cannot be recovered."""
        crm = CRM()
        for _ in range(5):
            crm.discover(prospect_id="p", company="Acme", industry="accounting",
                         state="NY")
        self.assertEqual(len(crm.all()), 1)


class SayingNoIsRemembered(unittest.TestCase):

    def setUp(self):
        self.crm = CRM()
        self.crm.discover(prospect_id="p", company="Acme",
                          industry="construction", state="TX")

    def test_disqualifying_without_a_reason_is_refused(self):
        with self.assertRaises(FailClosed):
            self.crm.disqualify("p", "   ")

    def test_a_disqualified_prospect_cannot_be_advanced(self):
        self.crm.disqualify("p", "they build bridges; we clean spreadsheets")
        with self.assertRaises(FailClosed):
            self.crm.advance("p", Standing.CONTACTED)

    def test_an_opted_out_prospect_is_not_contactable(self):
        self.crm.add_contact("p", contact())
        self.crm.opt_out("p")
        self.assertFalse(self.crm.get("p").contactable)

    def test_an_opt_out_blocks_further_research(self):
        self.crm.opt_out("p")
        with self.assertRaises(FailClosed):
            self.crm.add_evidence("p", published())

    def test_an_opt_out_blocks_holding_a_new_contact(self):
        self.crm.opt_out("p")
        with self.assertRaises(FailClosed):
            self.crm.add_contact("p", contact())

    def test_an_opt_out_cannot_be_advanced_away_from(self):
        self.crm.opt_out("p")
        with self.assertRaises(FailClosed):
            self.crm.advance("p", Standing.QUALIFIED)


class ItSurvivesARestart(unittest.TestCase):
    """`learn` appends; current state is the newest row, not the first."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.path = str(pathlib.Path(self.box.name) / "crm.db")

    def open(self):
        store = Store(self.path)
        self.addCleanup(store.close)
        return CRM(BusinessMemory(store, AuditLog(store)))

    def test_an_in_memory_crm_does_not_claim_to_be_durable(self):
        self.assertFalse(CRM().durable)

    def test_everything_learned_survives(self):
        crm = self.open()
        self.assertTrue(crm.durable)
        crm.discover(prospect_id="p1", company="Acme Builders LLC",
                     industry="construction", state="TX")
        crm.add_evidence("p1", published())
        crm.add_contact("p1", contact())
        crm.advance("p1", Standing.QUALIFIED, "fits csv-cleanup")

        after = self.open().get("p1")
        self.assertIs(after.standing, Standing.QUALIFIED,
                      "the restart restored an older state")
        self.assertTrue(after.verified_need)
        self.assertEqual(len(after.evidence), 1)
        self.assertEqual(len(after.contacts), 1)

    def test_an_opt_out_survives(self):
        """The one that matters most to get right."""
        crm = self.open()
        crm.discover(prospect_id="p", company="Acme", industry="accounting",
                     state="NY")
        crm.add_contact("p", contact())
        crm.opt_out("p")
        after = self.open().get("p")
        self.assertIs(after.standing, Standing.OPTED_OUT)
        self.assertFalse(after.contactable)

    def test_several_prospects_all_come_back(self):
        crm = self.open()
        crm.discover(prospect_id="p1", company="Acme", industry="construction",
                     state="TX")
        crm.discover(prospect_id="p2", company="Beta Realty",
                     industry="real-estate", state="CA")
        crm.discover(prospect_id="p3", company="Gamma Books",
                     industry="accounting", state="NY")
        self.assertEqual(len(self.open().all()), 3)

    def test_a_rediscovery_after_a_restart_is_still_a_duplicate(self):
        crm = self.open()
        crm.discover(prospect_id="p1", company="Acme Builders, LLC",
                     industry="construction", state="TX")
        later = self.open()
        later.discover(prospect_id="p2", company="ACME Builders LLC",
                       industry="construction", state="TX")
        self.assertEqual(len(later.all()), 1,
                         "the same company was recorded twice across a restart")


class TheCountsAreReadable(unittest.TestCase):

    def test_counts_separate_verified_from_merely_recorded(self):
        crm = CRM()
        crm.discover(prospect_id="a", company="A", industry="construction",
                     state="TX")
        crm.add_evidence("a", published())
        crm.discover(prospect_id="b", company="B", industry="accounting",
                     state="NY")
        crm.add_evidence("b", inferred())
        counts = crm.counts()
        self.assertEqual(counts["prospects"], 2)
        self.assertEqual(counts["with_a_verified_need"], 1)

    def test_every_industry_appears_even_at_zero(self):
        counts = CRM().counts()
        for industry in INDUSTRIES:
            self.assertIn(industry, counts["by_industry"])


if __name__ == "__main__":
    unittest.main()
