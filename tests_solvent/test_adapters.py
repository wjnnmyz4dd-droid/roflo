"""§10, §42, §43. Real adapters, tested without touching the network.

The fixture in ``fixtures/grants_gov_search2.json`` is a **recorded real
response** from api.grants.gov, trimmed to two records. That matters: an
adapter tested only against a fixture somebody wrote to match their own parser
is an adapter tested against its own assumptions.

Everything here is offline. No test makes a request, and the adapters cannot
make one anyway -- Discovery supplies the fetch and it goes through the Action
Gate.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from solvent import sourceaccess as access
from solvent.adapters import (ADAPTERS, VERIFIED_ON, GrantsGovSource,
                              SamGovOpportunitiesSource)
from solvent.discovery import Compliance, Readiness
from solvent.harness import OWNER, Solvent

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
GRANTS = json.loads((FIXTURES / "grants_gov_search2.json").read_text())

SAM = {"totalRecords": 1, "opportunitiesData": [{
    "noticeId": "abc123", "title": "Data cleansing services",
    "solicitationNumber": "W912-26-R-0001", "type": "Solicitation",
    "postedDate": "2026-09-01", "responseDeadLine": "2026-10-15",
    "fullParentPathName": "DEPT OF DEFENSE.DEPT OF THE ARMY",
    "naicsCode": "541511", "uiLink": "https://sam.gov/opp/abc123/view",
    "description": "Clean and deduplicate a supplied dataset."}]}


class TheGrantsGovAdapter(unittest.TestCase):

    def setUp(self):
        self.source = GrantsGovSource(keyword="data", rows=2)

    def test_it_parses_a_real_recorded_response(self):
        found = self.source.parse(GRANTS)
        self.assertEqual(len(found), 2)

    def test_every_candidate_has_an_identity_to_deduplicate_on(self):
        for opportunity in self.source.parse(GRANTS):
            self.assertTrue(opportunity.external_ref)
            self.assertTrue(opportunity.title)

    def test_the_posting_is_quarantined(self):
        for opportunity in self.source.parse(GRANTS):
            self.assertIsNotNone(opportunity.posting)

    def test_an_unstated_budget_is_zero_and_not_invented(self):
        """§51. The search response carries no award amount, so nothing here
        may supply one."""
        for opportunity in self.source.parse(GRANTS):
            self.assertEqual(opportunity.quoted_cents, 0)

    def test_the_agency_is_recorded_as_given_and_not_as_a_client(self):
        refs = [o.client_ref for o in self.source.parse(GRANTS)]
        self.assertTrue(all(r.startswith("agency:") for r in refs), refs)

    def test_it_links_back_to_the_source_record(self):
        for opportunity in self.source.parse(GRANTS):
            self.assertIn("grants.gov", opportunity.external_url)

    def test_the_request_is_the_documented_one(self):
        request = self.source.request()
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"],
                         "https://api.grants.gov/v1/api/search2")
        self.assertIn("keyword", request["body"])

    def test_it_needs_no_credential(self):
        self.assertFalse(self.source.auth_required)
        self.assertNotIn("requires_credential", self.source.request())

    def test_it_says_what_kind_of_work_it_returns(self):
        """A grant is not a client paying for a deliverable. Saying so is the
        difference between the owner choosing it and being surprised by it."""
        self.assertEqual(self.source.work_type, "GRANT_FUNDING")

    def test_it_records_when_its_terms_were_read(self):
        self.assertEqual(self.source.verified_on, VERIFIED_ON)
        self.assertIn("2026", self.source.terms_notes)


class TheGrantsGovAdapterFailsClosed(unittest.TestCase):
    """§42. A response that is not the documented shape is never guessed at."""

    def setUp(self):
        self.source = GrantsGovSource()

    def test_canary_the_good_fixture_does_parse(self):
        """Without this, every refusal below could hold on an adapter that
        never parses anything."""
        self.assertEqual(len(self.source.parse(GRANTS)), 2)

    def test_a_missing_data_object(self):
        with self.assertRaises(ValueError):
            self.source.parse({"errorcode": 0})

    def test_a_missing_hits_array(self):
        with self.assertRaises(ValueError):
            self.source.parse({"errorcode": 0, "data": {}})

    def test_hits_that_are_not_a_list(self):
        with self.assertRaises(ValueError):
            self.source.parse({"errorcode": 0, "data": {"oppHits": "nope"}})

    def test_an_error_code_is_not_ignored(self):
        with self.assertRaises(ValueError) as caught:
            self.source.parse({"errorcode": 1, "msg": "bad request",
                               "data": {"oppHits": []}})
        self.assertIn("errorcode", str(caught.exception))

    def test_a_record_with_no_identity(self):
        broken = {"errorcode": 0, "data": {"oppHits": [{"title": "x"}]}}
        with self.assertRaises(ValueError) as caught:
            self.source.parse(broken)
        self.assertIn("cannot be deduplicated", str(caught.exception))

    def test_a_record_with_no_title(self):
        broken = {"errorcode": 0, "data": {"oppHits": [{"id": "1"}]}}
        with self.assertRaises(ValueError):
            self.source.parse(broken)

    def test_html_instead_of_json(self):
        with self.assertRaises(Exception):
            self.source.parse("<html><body>login</body></html>")

    def test_an_empty_result_set_is_not_an_error(self):
        """Nothing found is a normal answer, not a broken source."""
        self.assertEqual(
            self.source.parse({"errorcode": 0, "data": {"oppHits": []}}), [])


class TheSamGovAdapter(unittest.TestCase):

    def setUp(self):
        self.source = SamGovOpportunitiesSource(
            posted_from="09/01/2026", posted_to="09/29/2026")

    def test_it_parses_the_documented_shape(self):
        found = self.source.parse(SAM)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].external_ref, "abc123")

    def test_it_declares_that_it_needs_a_key(self):
        self.assertTrue(self.source.auth_required)
        self.assertEqual(self.source.request()["requires_credential"],
                         "SAM_GOV_API_KEY")

    def test_the_adapter_carries_no_credential_itself(self):
        """An adapter holding a key is an adapter that can leak one.

        It *names* the credential it needs, which is the point -- the registry
        can then show "not configured" rather than the source merely failing.
        What it must not carry is a value, so the assertion is about the
        request's parameters, not about whether the words appear anywhere.
        """
        request = self.source.request()
        self.assertEqual(request["requires_credential"], "SAM_GOV_API_KEY")
        params = request["params"]
        self.assertNotIn("api_key", params)
        for value in params.values():
            self.assertNotRegex(str(value), r"[A-Za-z0-9]{24,}")

    def test_it_is_contract_work_not_funding(self):
        self.assertEqual(self.source.work_type, "CONTRACT_WORK")

    def test_the_documented_constraints_are_respected(self):
        self.assertLessEqual(
            SamGovOpportunitiesSource(limit=99_999).limit, 1000)

    def test_it_fails_closed_on_a_missing_array(self):
        with self.assertRaises(ValueError):
            self.source.parse({"totalRecords": 0})

    def test_it_fails_closed_on_a_record_with_no_notice_id(self):
        with self.assertRaises(ValueError):
            self.source.parse({"opportunitiesData": [{"title": "x"}]})

    def test_an_award_amount_is_read_when_stated(self):
        payload = {"opportunitiesData": [dict(
            SAM["opportunitiesData"][0], award={"amount": "1234.50"})]}
        self.assertEqual(self.source.parse(payload)[0].quoted_cents, 123_450)

    def test_a_missing_award_is_zero_not_invented(self):
        self.assertEqual(self.source.parse(SAM)[0].quoted_cents, 0)


class AdaptersHoldNoAuthority(unittest.TestCase):

    def test_no_adapter_can_open_a_socket(self):
        import ast

        source = pathlib.Path("solvent/adapters.py").read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        for network in ("socket", "http", "urllib", "requests", "ssl",
                        "subprocess", "asyncio"):
            self.assertNotIn(network, imported)

    def test_no_adapter_decides_anything_consequential(self):
        source = pathlib.Path("solvent/adapters.py").read_text()
        for forbidden in ("may_deploy", "authorize_spend", "gate.request",
                          "register(", "amend(", "set_operating_mode",
                          "record_promotion", "grant("):
            self.assertNotIn(forbidden, source)

    def test_both_adapters_are_registered(self):
        self.assertEqual(sorted(ADAPTERS), ["grants_gov", "sam_gov"])


class AdaptersPlugIntoTheExistingDiscoveryAuthority(unittest.TestCase):
    """They are ordinary WorkSources. Being real earns them nothing."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.s = Solvent(str(pathlib.Path(self.dir.name) / "solvent.db"))
        self.addCleanup(self.s.store.close)
        self.s.discovery.register_source(
            GrantsGovSource(), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED,
            determination="grants.gov API guide: search2 needs no auth")

    def test_registration_grants_only_the_read_side(self):
        granted = self.s.discovery.permissions("grants_gov")
        self.assertEqual(set(granted), set(access.DEFAULT_PERMISSIONS))
        for consequential in access.CONSEQUENTIAL:
            self.assertNotIn(consequential, granted)

    def test_it_is_a_found_role_source(self):
        from solvent import intake

        state = self.s.discovery.source_state("grants_gov")
        self.assertEqual(intake.role_of(state), intake.FOUND)

    def test_it_cannot_insert_owner_provenance(self):
        from solvent.errors import FailClosed

        with self.assertRaises(FailClosed):
            self.s.discovery.insert("grants_gov", owner_identity=OWNER)

    def test_polling_it_without_the_gate_yields_nothing(self):
        """It needs an external fetch, the Gate refuses in simulation, and so
        no opportunity appears. Discovery has no fallback path."""
        self.s.policy.amend({"discovery": {"approved_sources": ["grants_gov"]}},
                            OWNER, "approve")
        self.assertEqual(self.s.discovery.poll("grants_gov"), [])

    def test_the_registry_records_how_it_is_reached(self):
        state = self.s.discovery.source_state("grants_gov")
        self.assertEqual(state["access_method"], access.PUBLIC_FEED)
        self.assertEqual(state["auth_required"], 0)
        self.assertIn("grants.gov", state["base_url"])


if __name__ == "__main__":
    unittest.main()
