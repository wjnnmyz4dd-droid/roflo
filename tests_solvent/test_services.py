"""Service selection is strategic. It never becomes a second Governor."""

import pathlib
import unittest

from solvent.services import (
    CANDIDATES, DIMENSIONS, SPREADSHEET_CLEANUP, recommended, scorecard,
)


class Scorecard(unittest.TestCase):
    def test_the_scorecard_is_deterministic(self):
        self.assertEqual([c.id for c, _ in scorecard()],
                         [c.id for c, _ in scorecard()])

    def test_every_candidate_is_scored_on_every_dimension(self):
        for candidate in CANDIDATES:
            missing = set(DIMENSIONS) - set(candidate.scores)
            with self.subTest(service=candidate.id):
                self.assertEqual(missing, set(), f"{candidate.id} missing {missing}")

    def test_scores_are_within_the_stated_range(self):
        for candidate in CANDIDATES:
            for dimension, value in candidate.scores.items():
                with self.subTest(service=candidate.id, dimension=dimension):
                    self.assertIn(value, range(1, 6))

    def test_provability_outweighs_headline_rate(self):
        """For a first service, being able to prove the work matters most."""
        self.assertGreater(DIMENSIONS["verifiability"][0],
                           DIMENSIONS["expected_margin"][0])
        self.assertGreater(DIMENSIONS["liability"][0],
                           DIMENSIONS["expected_margin"][0])

    def test_the_recommendation_is_the_highest_scoring_candidate(self):
        service_1, backup = recommended()
        ranked = scorecard()
        self.assertEqual(service_1.id, ranked[0][0].id)
        self.assertEqual(backup.id, ranked[1][0].id)

    def test_the_high_liability_options_do_not_win(self):
        """A first job should be boring, not lucrative."""
        service_1, _ = recommended()
        self.assertNotIn(service_1.id, {"business_analysis", "research_summary"})


class NotASecondGovernor(unittest.TestCase):
    def test_the_module_prices_nothing(self):
        source = pathlib.Path("solvent/services.py").read_text()
        for forbidden in ("margin_floor", "Cents", "money(", "effective_cost",
                          "calibration", "authorize_spend"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_service_definition_carries_no_price(self):
        for field in SPREADSHEET_CLEANUP.__slots__:
            with self.subTest(field=field):
                self.assertNotIn("price_cents", field)
                self.assertNotIn("rate", field)

    def test_pricing_inputs_are_named_but_not_valued(self):
        """The Governor prices the job; the service says what to price on."""
        self.assertIn("row count", SPREADSHEET_CLEANUP.pricing_inputs)
        for entry in SPREADSHEET_CLEANUP.pricing_inputs:
            self.assertFalse(any(ch.isdigit() for ch in entry), entry)


class ServiceContract(unittest.TestCase):
    def test_an_in_scope_request_conforms(self):
        ok, _ = SPREADSHEET_CLEANUP.conforms(
            requested_formats=["csv"], requested=["clean and total by category"])
        self.assertTrue(ok)

    def test_the_contract_advertises_only_formats_the_capability_can_read(self):
        """Advertising a format is promising work.

        The contract listed xlsx and tsv while csv-cleanup/1.0 reads CSV only.
        An audit fed it a real .xlsx: the job conformed, executed, passed every
        check and delivered binary garbage, because the checks compare source to
        output and both were the same garbage.
        """
        self.assertEqual(SPREADSHEET_CLEANUP.supported_formats, ("csv",))
        ok, why = SPREADSHEET_CLEANUP.conforms(
            requested_formats=["xlsx"], requested=["remove duplicate rows"])
        self.assertFalse(ok)
        self.assertIn("unsupported format", why)

    def test_an_unsupported_format_is_refused(self):
        ok, why = SPREADSHEET_CLEANUP.conforms(
            requested_formats=["pdf"], requested=["clean it"])
        self.assertFalse(ok)
        self.assertIn("unsupported format", why)

    def test_requests_outside_the_service_are_refused(self):
        for ask in ("give me tax filing advice", "provide a valuation",
                    "write a macro", "do a legal review"):
            with self.subTest(ask=ask):
                ok, why = SPREADSHEET_CLEANUP.conforms(
                    requested_formats=["csv"], requested=[ask])
                self.assertFalse(ok, why)

    def test_an_empty_request_does_not_conform(self):
        """Unknown is not conforming."""
        ok, _ = SPREADSHEET_CLEANUP.conforms(requested_formats=["csv"], requested=[])
        self.assertFalse(ok)

    def test_the_contract_names_objective_verification(self):
        joined = " ".join(SPREADSHEET_CLEANUP.verification_requirements)
        self.assertIn("T1", joined)
        self.assertIn("T3", joined)
        self.assertIn("reconcile", joined)

    def test_the_contract_names_its_rejection_conditions(self):
        self.assertTrue(SPREADSHEET_CLEANUP.rejection_conditions)
        self.assertTrue(SPREADSHEET_CLEANUP.owner_approval_triggers)


if __name__ == "__main__":
    unittest.main()
