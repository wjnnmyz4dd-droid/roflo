"""The door the relationship layer never had.

``CRM.discover`` had no caller anywhere in ``solvent/``: no scheduled
task, no CLI command, no web route. The CRM, the negotiation engine,
the engagement and three scheduled tasks all sat behind it, reachable
only from test code -- so the connectors built on top worked and had
nothing to work on.

The facts cannot come from the system. A storefront order carries a
client reference and a form; it does not carry who the client is, and
a company name DeskPilot invented would be a forged record in the one
file whose purpose is to say what is known about a real person. So
they come from the owner, and these tests are mostly about what
happens when the owner has not supplied one.
"""

from __future__ import annotations

import unittest

from solvent import contracting, dealmaking
from solvent.clientintake import Refused, record_enquiry
from solvent.crm import Standing
from solvent.harness import OWNER
from solvent.negotiation import Outcome

from tests_solvent.test_dealmaking import Connected

SEEN = "2026-01-05"


class Intake(Connected):
    """A shop with an inbox registered, and nobody in the CRM."""

    def setUp(self):
        super().setUp()
        from solvent.discovery import Compliance, ManualSource, Readiness

        self.s.discovery.register_source(
            ManualSource("inbox", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED,
            determination="written enquiries the owner entered by hand")
        self.s.policy.amend(
            {"discovery": {"approved_sources": ["storefront", "inbox"]},
             "contracting": {"turnaround_days": 3}},
            OWNER, "the owner added an inbox")

    def enquiry(self, **over):
        fields = {
            "prospect_id": "client:northstar", "company": "Northstar Ltd",
            "industry": "construction", "state": "TX",
            "contact_name": "Dana", "contact_email": "dana@northstar.invalid",
            "wants": "csv-cleanup", "budget_cents": 60_000,
            "seen_on": SEEN, "owner_identity": OWNER,
        }
        fields.update(over)
        return record_enquiry(self.s, **fields)


class OneActNotFive(Intake):

    def test_the_client_is_recorded_with_everything_in_one_call(self):
        got = self.enquiry()

        self.assertTrue(got.done, got.summary())
        prospect = self.s.crm.get("client:northstar")
        self.assertEqual(prospect.company, "Northstar Ltd")
        self.assertEqual(prospect.industry, "construction")
        self.assertIs(prospect.standing, Standing.REPLIED)
        self.assertEqual([c.email for c in prospect.contacts],
                         ["dana@northstar.invalid"])
        self.assertTrue(prospect.evidence, "no evidence was recorded")

    def test_the_enquiry_becomes_an_opportunity(self):
        got = self.enquiry()

        self.assertTrue(got.opportunity_id)
        rows = [r for r in self.s.discovery.opportunities()
                if r["id"] == got.opportunity_id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_ref"], "client:northstar")
        self.assertEqual(int(rows[0]["quoted_cents"]), 60_000)

    def test_the_evidence_says_where_it_came_from(self):
        """A record that does not say it is hearsay reads as a fact."""
        self.enquiry()

        claims = self.s.crm.get("client:northstar").evidence
        self.assertTrue(any("owner" in c.source for c in claims),
                        [c.source for c in claims])
        self.assertTrue(any(c.basis.value == "CLIENT_STATED" for c in claims))

    def test_it_is_written_to_the_audit_log(self):
        self.enquiry()

        rows = self.s.audit.named(("client.enquiry_recorded",))
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["decision"], "RECORDED")


class WhatTheOwnerHasNotSaid(Intake):

    def test_every_missing_detail_is_named(self):
        for field in ("company", "industry", "state", "contact_email",
                      "wants", "seen_on"):
            with self.subTest(field):
                got = self.enquiry(**{field: "  "})
                self.assertIs(got.refusal, Refused.MISSING_DETAIL)
                self.assertIn(field, got.why)

    def test_a_negative_budget_is_refused(self):
        self.assertIs(self.enquiry(budget_cents=-1).refusal,
                      Refused.MISSING_DETAIL)

    def test_only_the_owner_may_enter_a_client(self):
        got = self.enquiry(owner_identity="owner:somebody-else")

        self.assertIs(got.refusal, Refused.NOT_THE_OWNER)
        with self.assertRaises(Exception):
            self.s.crm.get("client:northstar")

    def test_a_skill_the_shop_does_not_sell_is_refused_before_recording(self):
        """An address held for work that cannot be done is an address held."""
        got = self.enquiry(wants="time-travel")

        self.assertIs(got.refusal, Refused.NOT_A_SKILL)
        with self.assertRaises(Exception):
            self.s.crm.get("client:northstar")

    def test_an_unregistered_source_is_refused(self):
        got = self.enquiry(source="a-board-nobody-registered")

        self.assertIs(got.refusal, Refused.NO_SOURCE)
        self.assertIn("registers one", got.why)

    def test_a_client_already_held_is_not_overwritten(self):
        self.assertTrue(self.enquiry().done)

        again = self.enquiry(company="A Different Name")

        self.assertIs(again.refusal, Refused.ALREADY_KNOWN)
        self.assertEqual(self.s.crm.get("client:northstar").company,
                         "Northstar Ltd")

    def test_a_company_already_held_under_another_id_is_reported(self):
        """The CRM deduplicates on company and state. The owner decides."""
        self.assertTrue(self.enquiry().done)

        twin = self.enquiry(prospect_id="client:northstar-two")

        self.assertIs(twin.refusal, Refused.ALREADY_KNOWN)
        self.assertIn("client:northstar", twin.why)

    def test_somebody_who_opted_out_stays_opted_out(self):
        """An enquiry typed in is not a withdrawal of an opt-out."""
        self.assertTrue(self.enquiry().done)
        self.s.crm.opt_out("client:northstar", "they asked us to stop")

        again = self.enquiry()

        self.assertIs(again.refusal, Refused.OPTED_OUT)
        self.assertIs(self.s.crm.get("client:northstar").standing,
                      Standing.OPTED_OUT)


class AndThenTheyGetAPrice(Intake):
    """The whole point: the layer behind the door now runs."""

    def test_a_recorded_enquiry_is_priced_by_the_scheduler(self):
        self.assertTrue(self.enquiry().done)

        said = self.deals(self.runtime())

        self.assertIn("1 opened", said)
        deal = self.s.chief_of_staff.negotiations["client:northstar"]
        self.assertEqual(deal.service, "csv-cleanup")
        self.assertIs(deal.outcome, Outcome.OPEN)

    def test_a_discovered_opportunity_is_enough(self):
        """It used to need ACCEPTED, which only a job commitment sets.

        ``OpportunityStatus.ACCEPTED`` is written in exactly one
        place -- ``qualification.select`` -- which also mints a job
        and applies the Governor's verdict. So the precondition for
        quoting a client was a commitment to doing their job.
        """
        got = self.enquiry()
        row = [r for r in self.s.discovery.opportunities()
               if r["id"] == got.opportunity_id][0]
        self.assertEqual(row["status"], "DISCOVERED")
        self.assertIsNone(row["job_id"], "a job was minted for a quote")

        found = dealmaking.candidates(self.s)

        self.assertEqual([c.prospect_id for c in found], ["client:northstar"])

    def test_a_rejected_opportunity_is_still_not_priced(self):
        from solvent.discovery import OpportunityStatus

        got = self.enquiry()
        self.s.discovery.set_status(got.opportunity_id,
                                    OpportunityStatus.REJECTED)

        self.assertEqual(dealmaking.candidates(self.s), [])

    def test_the_whole_chain_runs_from_one_typed_enquiry(self):
        """Enquiry to contract, with nothing inserted in the middle."""
        from solvent.engagement import Acceptance

        self.assertTrue(self.enquiry().done)
        self.deals(self.runtime())
        deal = self.s.chief_of_staff.negotiations["client:northstar"]
        deal.respond(deal.current_cents, why="the client accepted")

        drawn = contracting.propose(self.s, prospect_id="client:northstar",
                                    initiator="test")

        self.assertTrue(drawn.done, drawn.summary())
        engagement = self.s.chief_of_staff.engagements["client:northstar"]
        engagement.accept(Acceptance("Dana", SEEN, "reply", "sha256:x"))
        request = engagement.request_deposit()
        engagement.record_payment({"verified": True,
                                   "cents": request["amount_cents"],
                                   "external_ref": "pi_dep"})

        self.assertTrue(engagement.work_authorised)
        self.assertEqual(engagement.proposal.price_cents, deal.current_cents)



class OneClientCannotBlockTheQueue(Intake):
    """Found by a twelve-day simulated run, not by reading the code.

    ``dealmaking.open_ready`` stopped after ``limit`` *candidates
    tried* rather than ``limit`` opened, and ``candidates`` is
    deterministically ordered. A prospect whose budget sat under the
    owner's floor was therefore refused on every tick for ever, and
    nobody behind them was ever reached: measured at 1,584 identical
    refusals across eleven simulated days, with the shop's whole
    relationship channel producing nothing.

    Written here rather than in ``test_dealmaking`` because this is
    the door the blocked clients came through, and because the
    storefront fixture gives every order the same ``client_ref`` --
    two orders there are one prospect, and a queue of one cannot
    show a queue defect.
    """

    #: Under the floor the owner's recorded costs imply, and over the
    #: storefront's published minimum, which is the gap the blocked
    #: client lived in.
    TOO_LITTLE = 5_000
    PLENTY = 70_000

    def two_clients(self):
        """One the shop cannot serve, one it can."""
        self.assertTrue(self.enquiry(prospect_id="client:too-cheap",
                                     company="Too Cheap Ltd",
                                     budget_cents=self.TOO_LITTLE).done)
        self.assertTrue(self.enquiry(prospect_id="client:can-pay",
                                     company="Can Pay Ltd",
                                     budget_cents=self.PLENTY).done)

    def test_both_clients_are_candidates(self):
        """The control: a queue of one proves nothing about queues."""
        self.two_clients()

        found = {c.prospect_id for c in dealmaking.candidates(self.s)}

        self.assertEqual(found, {"client:too-cheap", "client:can-pay"})

    def test_the_cheap_one_really_is_refused(self):
        """The other control: if it were affordable there is no queue."""
        self.two_clients()

        tried = dealmaking.open_ready(self.s, limit=2, initiator="test")
        cheap = [o for o in tried if o.prospect_id == "client:too-cheap"]

        self.assertTrue(cheap)
        self.assertFalse(cheap[0].opened, cheap[0].summary())

    def test_a_refusal_does_not_spend_the_ticks_budget(self):
        self.two_clients()

        tried = dealmaking.open_ready(self.s, limit=1, initiator="test")

        self.assertEqual(len(tried), 2,
                         "the sweep stopped at the refused client: "
                         + "; ".join(o.summary() for o in tried))
        opened = [o for o in tried if o.opened]
        self.assertEqual([o.prospect_id for o in opened], ["client:can-pay"])

    def test_the_bound_it_was_protecting_still_holds(self):
        """At most `limit` negotiations are actually opened."""
        for nth in range(3):
            self.assertTrue(self.enquiry(
                prospect_id=f"client:payer-{nth}",
                company=f"Payer {nth} Ltd", budget_cents=self.PLENTY).done)

        tried = dealmaking.open_ready(self.s, limit=1, initiator="test")

        self.assertEqual(len([o for o in tried if o.opened]), 1)

    def test_a_limit_of_zero_opens_nothing(self):
        self.two_clients()

        tried = dealmaking.open_ready(self.s, limit=0, initiator="test")

        self.assertEqual([o for o in tried if o.opened], [])


if __name__ == "__main__":
    unittest.main()
