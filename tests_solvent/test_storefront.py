"""The inbound channel, and the rule that it is not a second engine.

Two ways of learning about work, one way of doing it. An order placed on a
storefront and an opportunity found on a board must be the same thing the
moment they are understood: a second execution path would need its own
qualification, its own Governor call, its own verification tier and its own
audit trail, and the day those drifted is the day one started taking work
the other would have refused.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from tests_solvent.fixtures import give_an_issuer
from solvent import storefront as sf
from solvent.discovery import Compliance, Readiness
from solvent.errors import FailClosed
from solvent.harness import (
    OWNER, Solvent, owner_promotions, promote_capability, run_business_cycle,
)
from solvent.types import Criticality, Requirement, RequirementSource

CAPABILITIES = ("csv-cleanup", "spreadsheet-automation", "document-processing",
                "data-entry", "report-builder")


def req(rid, check, params=None):
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params=params or {},
        criticality=Criticality.MANDATORY)


class Shop(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "shop.db"))
        give_an_issuer(self.s)
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the storefront")
        self.shop = sf.Storefront(self.s.capability, self.s.policy)
        self.s.discovery.register_source(
            self.shop.source, owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="inbound orders")
        self.s.policy.amend({
            "discovery": {"approved_sources": ["storefront"]},
            "financial": {"max_job_spend_cents": 500_000,
                          "owner_approval_above_cents": 10_000_000},
            "commercial": {"minimum_project_fee_cents": 4_000},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
        }, OWNER, "test: storefront")

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def complete_order(self, order_id="o1", offer_id="csv-cleanup",
                       cents=15_000):
        return self.shop.place(
            order_id=order_id, offer_id=offer_id, client_ref="client:acme",
            answers={"source_file": "customers.csv",
                     "rules": "remove exact duplicate rows",
                     "calculations": "Amount = Units x Rate",
                     "formatting": "two decimal places",
                     "template": "letter.docx",
                     "missing_values": "stop and ask",
                     "field_mapping": "Name -> FullName",
                     "group_by": "month", "measures": "total Amount"},
            offered_cents=cents)


class EveryOfferMatchesACertifiedSkill(Shop):

    def test_there_is_exactly_one_offer_per_skill(self):
        self.assertEqual(sorted(o.capability for o in sf.OFFERS),
                         sorted(CAPABILITIES))

    def test_no_offer_advertises_an_uncertified_capability(self):
        for service in sf.OFFERS:
            with self.subTest(offer=service.offer_id):
                sellable, why = self.shop.sellable(service.offer_id)
                self.assertTrue(sellable, why)

    def test_withdrawing_a_skill_closes_its_offer(self):
        """An advert is a promise, so it must follow the certification."""
        registry = self.s.capability
        original = registry.may_deploy
        registry.may_deploy = lambda n: (
            (False, "not cleared") if n == "report-builder" else original(n))
        self.addCleanup(setattr, registry, "may_deploy", original)

        sellable, why = self.shop.sellable("report-builder")
        self.assertFalse(sellable)
        self.assertIn("report-builder", why)

    def test_an_offer_below_the_owners_floor_cannot_be_shown(self):
        self.s.policy.amend(
            {"commercial": {"minimum_project_fee_cents": 20_000}}, OWNER,
            "test: the owner raises the floor above every offer")
        for service in sf.OFFERS:
            with self.subTest(offer=service.offer_id):
                sellable, why = self.shop.sellable(service.offer_id)
                self.assertFalse(sellable)
                self.assertIn("minimum", why)

    def test_nothing_is_published(self):
        """Putting these in front of real customers is the owner's call."""
        for service in sf.OFFERS:
            with self.subTest(offer=service.offer_id):
                self.assertFalse(service.published)

    def test_no_code_here_can_publish(self):
        import inspect

        source = inspect.getsource(sf)
        self.assertNotIn("published = True", source)
        self.assertNotIn("published=True", source)

    def test_every_offer_says_what_it_will_not_do(self):
        for service in sf.OFFERS:
            with self.subTest(offer=service.offer_id):
                self.assertTrue(service.scope_excluded,
                                "an offer with no stated exclusions is an "
                                "argument with a customer waiting to happen")
                self.assertTrue(service.deliverables)
                self.assertTrue(service.terms)
                self.assertTrue(service.turnaround)

    def test_no_offer_promises_instant_delivery(self):
        """The work is fast. The owner's approval is not."""
        for service in sf.OFFERS:
            with self.subTest(offer=service.offer_id):
                self.assertNotIn("instant", service.turnaround.lower())
                self.assertNotIn("immediate", service.turnaround.lower())

    def test_every_offer_asks_for_the_source_file(self):
        for service in sf.OFFERS:
            with self.subTest(offer=service.offer_id):
                self.assertIn("source_file", service.intake_keys())


class IntakeRefusesToGuess(Shop):

    def test_a_missing_required_answer_stops_the_order(self):
        order = self.shop.place(
            order_id="o1", offer_id="csv-cleanup", client_ref="client:acme",
            answers={"source_file": "customers.csv"})
        self.assertIs(order.state, sf.OrderState.NEEDS_INFORMATION)
        self.assertIn("rules", order.missing)

    def test_the_customer_is_told_what_is_missing_in_their_words(self):
        order = self.shop.place(
            order_id="o1", offer_id="csv-cleanup", client_ref="client:acme",
            answers={})
        status = order.customer_status()
        self.assertIn("source_file", status)
        self.assertNotIn("NEEDS_INFORMATION", status)

    def test_an_optional_answer_does_not_block(self):
        order = self.complete_order()
        self.assertIs(order.state, sf.OrderState.RECEIVED)

    def test_an_order_below_the_offer_minimum_is_declined_with_the_price(self):
        order = self.complete_order(cents=500)
        self.assertIs(order.state, sf.OrderState.DECLINED)
        self.assertIn("40.00", order.why)

    def test_an_unknown_offer_is_refused(self):
        with self.assertRaises(FailClosed):
            self.shop.place(order_id="o1", offer_id="moon-landing",
                            client_ref="c", answers={})

    def test_the_same_order_cannot_be_taken_twice(self):
        self.complete_order()
        with self.assertRaises(FailClosed) as caught:
            self.complete_order()
        self.assertIn("two jobs for one payment", str(caught.exception))


class OneOrderBecomesOneOpportunity(Shop):

    def test_submitting_hands_it_to_discovery(self):
        self.complete_order()
        order = self.shop.submit_to_pipeline("o1")
        self.assertIs(order.state, sf.OrderState.IN_QUALIFICATION)
        self.assertTrue(order.opportunity_id)

    def test_an_incomplete_order_cannot_be_submitted(self):
        self.shop.place(order_id="o1", offer_id="csv-cleanup",
                        client_ref="c", answers={})
        with self.assertRaises(FailClosed):
            self.shop.submit_to_pipeline("o1")

    def test_an_order_cannot_enter_the_pipeline_twice(self):
        self.complete_order()
        self.shop.submit_to_pipeline("o1")
        with self.assertRaises(FailClosed) as caught:
            self.shop.submit_to_pipeline("o1")
        self.assertIn("two jobs for one payment", str(caught.exception))

    def test_the_storefront_cannot_execute_anything_itself(self):
        """There is no method here that does the work."""
        for name in dir(self.shop):
            with self.subTest(method=name):
                for forbidden in ("execute", "deliver", "run_", "invoice",
                                  "charge", "collect"):
                    self.assertFalse(
                        name.startswith(forbidden),
                        f"Storefront.{name} looks like a second execution "
                        "path")


class BothChannelsRunTheSamePipeline(Shop):

    def source_file(self):
        path = self.dir / "customers.csv"
        rows = ["Name,Email,Amount"]
        for i in range(300):
            rows.append(f"  C{i % 40} ,c{i % 40}@x.test,{i % 17}.50")
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def test_an_inbound_order_completes_the_whole_existing_cycle(self):
        self.complete_order()
        self.shop.submit_to_pipeline("o1")
        cycle = run_business_cycle(
            self.s, source_name="storefront",
            source_file=str(self.source_file()), workdir=self.box.name,
            capability="csv-cleanup",
            agreed_requirements=[
                req("R1", "require_columns",
                    {"columns": ["Name", "Email", "Amount"]}),
                req("R2", "drop_exact_duplicates", {})],
            owner_accepts_quote=True, simulate_payment=True)
        self.assertTrue(cycle.completed,
                        f"{cycle.stopped_at}: {cycle.why_stopped}")
        self.assertEqual(cycle.invoiced_cents, 15_000)

    def test_it_runs_the_same_stages_as_a_discovered_job(self):
        from solvent.harness import stages_without_conditionals

        self.complete_order()
        self.shop.submit_to_pipeline("o1")
        cycle = run_business_cycle(
            self.s, source_name="storefront",
            source_file=str(self.source_file()), workdir=self.box.name,
            capability="csv-cleanup",
            agreed_requirements=[req("R1", "drop_exact_duplicates", {})],
            owner_accepts_quote=True, simulate_payment=True)
        self.assertEqual(stages_without_conditionals(cycle.stages),
                         stages_without_conditionals(),
                         "the inbound channel skipped or added a stage")

    def test_the_storefront_source_is_a_work_source_like_any_other(self):
        from solvent.discovery import WorkSource

        self.assertIsInstance(self.shop.source, WorkSource)
        self.assertFalse(self.shop.source.is_fixture,
                         "real customer orders must not be marked fixture")

    def test_it_appears_as_an_active_source(self):
        active = [r["name"] for r in self.s.discovery.active_sources()]
        self.assertIn("storefront", active)


if __name__ == "__main__":
    unittest.main()
