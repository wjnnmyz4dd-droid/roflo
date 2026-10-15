"""The six-platform interface: one row per marketplace, seven activities.

The catalogue is the six now. This is the view that answers "what can
DeskPilot actually do on each of them?" without collapsing the seven
activities into one word per platform -- which is the mistake the split
exists to prevent, and the mistake that would let a row read
"integrated" because something about the platform was permitted.
"""

from __future__ import annotations

import unittest

from solvent import sourceaccess as access
from solvent import sourcecatalog as catalogue


class TheInterfaceIsExactlyTheSix(unittest.TestCase):

    def setUp(self):
        self.rows = catalogue.marketplace_interface()

    def test_six_rows(self):
        self.assertEqual(
            sorted(r["source_id"] for r in self.rows),
            ["contra", "fiverr", "freelancer", "guru", "peopleperhour",
             "upwork"])

    def test_every_row_covers_all_seven_activities(self):
        for row in self.rows:
            with self.subTest(source=row["source_id"]):
                self.assertEqual(sorted(row["activities"]),
                                 sorted(access.ACTIVITY_KINDS))

    def test_every_activity_has_a_finding_even_when_unread(self):
        """An activity nobody researched reads UNDETERMINED. It does not
        quietly vanish from the row, which is how a gap becomes invisible."""
        for row in self.rows:
            for label, finding in row["activities"].items():
                with self.subTest(source=row["source_id"], activity=label):
                    self.assertIn(finding["finding"],
                                  (catalogue.TERMS_PERMITTED,
                                   catalogue.TERMS_FORBIDDEN,
                                   catalogue.TERMS_UNDETERMINED))

    def test_a_permitted_activity_carries_its_citation(self):
        for row in self.rows:
            for label, finding in row["activities"].items():
                if finding["finding"] != catalogue.TERMS_PERMITTED:
                    continue
                with self.subTest(source=row["source_id"], activity=label):
                    self.assertTrue(finding["citation"].strip())
                    self.assertTrue(finding["read_on"].strip())

    def test_no_row_claims_an_automated_route_it_has_not_got(self):
        """The finding, asserted. Only Contra permits anything automated,
        and what it permits is reading its public pages -- which is not
        where its jobs are."""
        permitted = {r["source_id"]: r["permitted"] for r in self.rows}
        self.assertEqual(permitted["contra"], [access.DISCOVER])
        for source_id in ("upwork", "fiverr", "freelancer", "peopleperhour",
                          "guru"):
            with self.subTest(source=source_id):
                self.assertEqual(permitted[source_id], [])

    def test_manual_import_is_available_everywhere(self):
        """The one route that needs nobody's permission, on all six."""
        for row in self.rows:
            with self.subTest(source=row["source_id"]):
                self.assertTrue(row["manual_import_available"])

    def test_nothing_reads_as_ready(self):
        """None of the six is established. They are not all unread.

        This asserted UNVERIFIED for every platform, which conflated
        "nobody has looked" with "not ready". Freelancer's agreement
        has now been read and forbids automated API access without
        express written permission, so it reads BLOCKED - PLATFORM
        RESTRICTION: a stronger refusal than UNVERIFIED, and one the
        old assertion would have called a regression.
        """
        allowed = {catalogue.UNVERIFIED,
                   catalogue.BLOCKED_PLATFORM_RESTRICTION}
        for row in self.rows:
            with self.subTest(source=row["source_id"]):
                # One of exactly two, not merely "not ready": a status
                # nobody listed here is a new state that needs looking
                # at, and PENDING would mean a platform had quietly
                # acquired a determination.
                self.assertIn(row["status"], allowed)
                self.assertTrue(row["why"].strip())

    def test_freelancer_is_blocked_by_its_agreement_not_by_silence(self):
        """The 2026-10-15 reading, pinned.

        Section 33 of Freelancer's user agreement forbids automated
        access to the API "for any purpose without our express written
        permission". The endpoint answering 200 is not permission, and
        the catalogue has to say which of those two it is recording.
        """
        row = {r["source_id"]: r for r in self.rows}["freelancer"]
        self.assertEqual(row["status"],
                         catalogue.BLOCKED_PLATFORM_RESTRICTION)
        self.assertIn("express written permission", row["why"])


class TheSummaryIsHonest(unittest.TestCase):

    def setUp(self):
        self.text = catalogue.marketplace_summary()

    def test_it_names_all_six(self):
        for name in ("Upwork", "Fiverr", "Freelancer", "PeoplePerHour",
                     "Guru", "Contra"):
            self.assertIn(name, self.text)

    def test_it_says_what_is_unread_rather_than_leaving_it_blank(self):
        self.assertIn("nobody has read this", self.text)

    def test_it_does_not_use_the_word_integrated(self):
        """Nothing here is integrated, and a summary that says so about a
        platform permitting one activity would be the whole problem."""
        self.assertNotIn("integrated", self.text.lower())

    def test_it_leads_with_the_route_that_actually_works(self):
        self.assertIn("manual import", self.text.lower())


if __name__ == "__main__":
    unittest.main()
