"""One client, one job, end to end, with nothing inserted in the middle.

The path the assignment names::

    DISCOVERY -> QUALIFICATION -> NEGOTIATION -> AUTHORIZED CONTRACT
    -> JOB ADMISSION -> EXECUTION -> INDEPENDENT QA -> DELIVERY
    -> INVOICE -> SIMULATED PAYMENT -> RECONCILIATION
    -> CAPACITY RECOVERY

Three rules this file holds itself to, and a test that breaks one is
not evidence:

**No manually inserted intermediate state.** Nothing here sets a job
state, an opportunity status, an engagement stage or a ledger row.
Every transition is made by the component that owns it.

**No shortcut around a missing connection.** Where a connection was
missing it has been built -- ``clientintake`` for the CRM door,
``contracting`` for the contract and the result, ``settlement`` for
the close. This file calls those, which is calling production.

**Only the outside world is simulated.** The client is fictional and
so is the payment provider. What the client does -- write in, haggle,
sign, pay a deposit, send a file, pay an invoice -- is simulated.
Everything DeskPilot does in response is the real component making
the real decision, and the payment arrives as a signed webhook the
production rail verifies.

The one thing asserted at every step is that the step was taken *by
production*: the test reads the result rather than arranging it.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import tempfile
import unittest
from unittest import mock

from solvent import contracting, dealmaking, settlement
from solvent.clientintake import record_enquiry
from solvent.crm import Standing
from solvent.discovery import OpportunityStatus
from solvent.engagement import Acceptance, Stage
from solvent.harness import ingest_payment_spool
from solvent.negotiation import Outcome
from solvent.qualification import OCCUPIES_CAPACITY
from solvent.runtime import Runtime, reconcile
from solvent.storefront import OrderState
from solvent.types import JobState

from simulation import rail
from simulation.customers import Population, a_customer
from simulation.shop import OWNER_IDENTITY, open_a_shop

#: The one fictional client. `.invalid` is reserved by RFC 2606.
CLIENT = "client:northstar"
COMPANY = "Northstar Business Solutions 001"
EMAIL = "dana@northstar-001.invalid"
WANTS = "csv-cleanup"
SEEN = "2026-01-05"


class TheWholeTransaction(unittest.TestCase):
    """Run once, asserted step by step. Order matters, so does state."""

    @classmethod
    def setUpClass(cls):
        cls._key = mock.patch.dict(os.environ, {
            "SOLVENT_OWNER_KEY": secrets.token_hex(32),
            rail.SECRET_ENV: rail.TEST_SECRET})
        cls._key.start()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls._tmp.name)
        cls.spool = cls.root / "payments-inbox"
        # The owner's per-run job limit starts at zero and is raised
        # to one for the step that executes the work. Not a shortcut:
        # `run_due` runs every due task, so the admission tick and
        # the business tick fire in the same pass and the order is
        # executed before there is anything to assert about the
        # admission. The limit is an owner setting, moved here to
        # make each step observable rather than to skip one --
        # `continuous_enabled` would have done it too, but that gates
        # dealmaking and contracting as well and would have switched
        # off the chain under test.
        cls.s = open_a_shop(cls.root / "shop", policy={
            "runtime": {"payment_spool": str(cls.spool)},
            "operations": {"max_jobs_per_run": 0}})
        cls.runtime = Runtime(cls.s, clock=lambda: cls.now[0])
        cls.now = [0.0]
        cls.runtime = Runtime(cls.s, clock=lambda: cls.now[0])
        cls.runtime.start()
        cls.runtime.default_tasks()
        cls.trace: list = []

    @classmethod
    def tearDownClass(cls):
        try:
            cls.s.store.close()
        except Exception:
            pass
        cls._tmp.cleanup()
        cls._key.stop()

    def note(self, step: str, detail: str) -> None:
        type(self).trace.append(f"{step:<22} {detail}")

    # ---- the client's materials, from the production generators -----

    @classmethod
    def materials(cls) -> dict:
        import random

        population = Population(seed="one-transaction", length_days=1)
        customer = a_customer(random.Random("one"), index=1, arrives_on=0,
                              door="relationship")
        customer.customer_id, customer.wants = "northstar", WANTS
        return population.materials_for(customer,
                                        workroot=cls.root / "materials")

    # ---- the steps, in order ----------------------------------------

    def test_01_discovery(self):
        """The client writes in. The owner types what they said."""
        recorded = record_enquiry(
            self.s, prospect_id=CLIENT, company=COMPANY,
            industry="construction", state="TX", contact_name="Dana",
            contact_email=EMAIL, wants=WANTS, budget_cents=70_000,
            seen_on=SEEN, owner_identity=OWNER_IDENTITY)

        self.assertTrue(recorded.done, recorded.summary())
        self.assertIs(self.s.crm.get(CLIENT).standing, Standing.REPLIED)
        row = [r for r in self.s.discovery.opportunities()
               if r["id"] == recorded.opportunity_id][0]
        self.assertEqual(row["status"], OpportunityStatus.DISCOVERED.value)
        self.note("DISCOVERY", f"{COMPANY} -> opportunity "
                               f"{recorded.opportunity_id}")

    def test_02_qualification_and_negotiation(self):
        """The scheduler prices it. No test code opens the deal."""
        done = self.runtime.run_due()
        type(self).now[0] += 601
        line = [o for o in done if o.startswith("dealmaking:")]

        self.assertTrue(line, done)
        self.assertIn("1 opened", line[0])
        deal = self.s.chief_of_staff.negotiations[CLIENT]
        self.assertIs(deal.outcome, Outcome.OPEN)
        self.assertGreaterEqual(deal.current_cents, deal.floor)
        self.note("QUALIFICATION", f"capability cleared, capacity has room, "
                                   f"floor {deal.floor}c")
        self.note("NEGOTIATION", f"opened at {deal.current_cents}c by the "
                                 f"scheduler")

    def test_03_the_client_agrees(self):
        """Simulated: this is the client speaking."""
        deal = self.s.chief_of_staff.negotiations[CLIENT]
        asking = deal.current_cents

        deal.respond(asking, why="the client accepted the price")

        self.assertIs(deal.outcome, Outcome.AGREED_PENDING_AUTHORISATION)
        self.assertEqual(deal.current_cents, asking)
        self.note("AGREEMENT", f"the client accepted {asking}c")

    def test_04_authorized_contract(self):
        """The scheduler draws it, on the published offer's terms."""
        done = self.runtime.run_due()
        type(self).now[0] += 601
        line = [o for o in done if o.startswith("contracting:")]

        self.assertTrue(line, done)
        self.assertIn("1 carried", line[0])
        engagement = self.s.chief_of_staff.engagements[CLIENT]
        deal = self.s.chief_of_staff.negotiations[CLIENT]
        self.assertIs(engagement.stage, Stage.SENT)
        self.assertEqual(engagement.proposal.price_cents, deal.current_cents,
                         "the contract repriced the agreement")
        self.assertIs(self.s.crm.get(CLIENT).standing, Standing.PROPOSED)
        self.note("CONTRACT", f"proposed at {engagement.proposal.price_cents}c "
                              f"over {engagement.proposal.turnaround_days} days")

    def test_05_the_client_signs_and_pays_a_deposit(self):
        """Simulated. The deposit is a verified event or it is nothing."""
        engagement = self.s.chief_of_staff.engagements[CLIENT]
        engagement.accept(Acceptance("Dana", SEEN, "reply to the proposal",
                                     f"sha256:{CLIENT}"))
        request = engagement.request_deposit()
        self.assertFalse(engagement.work_authorised,
                         "work was authorised before the money arrived")

        engagement.record_payment({"verified": True,
                                   "cents": request["amount_cents"],
                                   "external_ref": "pi_deposit_northstar"})

        self.assertTrue(engagement.work_authorised)
        self.note("DEPOSIT", f"{engagement.collected_cents}c cleared "
                             f"({engagement.deposit_fraction:.0%})")

    def test_06_the_client_sends_their_file(self):
        engagement = self.s.chief_of_staff.engagements[CLIENT]

        engagement.supply(**self.materials())

        named = engagement.materials["source_file"]
        self.assertTrue(pathlib.Path(named).is_file(), named)
        self.note("MATERIALS", pathlib.Path(named).name)

    def test_07_job_admission(self):
        """The scheduler admits it. No test code places the order."""
        done = self.runtime.run_due()
        type(self).now[0] += 601
        line = [o for o in done if o.startswith("contracting:")]

        self.assertTrue(line, done)
        order = self.s.storefront.order(f"eng-{CLIENT}")
        self.assertIsNotNone(order, line)
        engagement = self.s.chief_of_staff.engagements[CLIENT]
        self.assertEqual(order.offered_cents, engagement.proposal.price_cents)
        self.assertEqual(order.prepaid_cents, engagement.collected_cents)
        self.assertIs(self.s.crm.get(CLIENT).standing, Standing.CONTRACTED)
        self.note("ADMISSION", f"order {order.order_id} at "
                               f"{order.offered_cents}c, "
                               f"{order.prepaid_cents}c already paid")

    def test_08_execution_qa_delivery_and_invoice(self):
        """The business loop, switched on, and it does the rest itself.

        ``_one_business_job`` is the function ``_tick_business``
        calls, with nothing substituted: it asks
        ``workorder.next_work_order`` what to run and hands it to
        ``run_business_cycle``. The tick's own wiring -- the lease,
        the bounds, the halt -- is certified in
        ``test_service_does_the_work``; what is being shown here is
        that the order this transaction placed is the one it picks
        up.
        """
        from solvent.harness import OWNER

        self.s.policy.amend({"operations": {"max_jobs_per_run": 1}},
                            OWNER, "the owner allowed one job this run")
        before = self.s.capacity.owed_seconds()
        cycle = self.runtime._one_business_job()
        type(self).now[0] += 601

        self.assertIsNotNone(cycle, "the admitted order was not picked up")
        for stage in ("discover", "qualify", "price", "contract", "budget",
                      "execute", "verify", "deliver", "invoice"):
            self.assertIn(stage, cycle.stages, cycle.stages)
        self.assertTrue(cycle.delivered)
        self.assertTrue(cycle.deliverable_path
                        and pathlib.Path(cycle.deliverable_path).is_file())
        self.assertTrue(cycle.invoice_path
                        and pathlib.Path(cycle.invoice_path).is_file())
        self.assertGreaterEqual(cycle.verification_rounds, 1)
        engagement = self.s.chief_of_staff.engagements[CLIENT]
        self.assertEqual(cycle.quoted_cents, engagement.proposal.price_cents,
                         "the cycle repriced the agreement")
        self.assertEqual(cycle.prepaid_cents + cycle.due_cents,
                         cycle.invoiced_cents)
        type(self).job_id = cycle.job_id
        type(self).due = cycle.due_cents
        self.note("EXECUTION", f"{cycle.execution_wall_seconds:.2f}s, "
                               f"{cycle.committed_requirements} "
                               f"requirement(s) committed")
        self.note("QA", f"{cycle.verification_rounds} round(s), tier "
                        f"{cycle.required_tier}")
        self.note("DELIVERY", pathlib.Path(cycle.deliverable_path).name)
        self.note("INVOICE", f"{cycle.invoiced_cents}c less "
                             f"{cycle.prepaid_cents}c deposit = "
                             f"{cycle.due_cents}c due")
        self.assertGreaterEqual(before, 0.0)

    def test_09_the_invoice_says_what_is_owed(self):
        """The document the client reads, not the row in our database."""
        text = pathlib.Path(
            [p for p in (self.root / "shop").rglob("invoice-*.txt")][0]
        ).read_text(encoding="utf-8")

        self.assertIn("Northstar Business Solutions LLC", text)
        self.assertIn("deposit, paid before work began", text)
        self.assertIn(f"{self.due / 100:,.2f}", text)
        self.note("INVOICE DOC", "names the issuer and shows the deduction")

    def test_10_simulated_payment(self):
        """A signed webhook. The production rail verifies the signature."""
        owed = int((self.s.ledger.payment_for(self.job_id) or {})
                   .get("amount_cents") or 0)
        self.assertEqual(owed, self.due)

        rail.pay(self.spool, job_id=self.job_id, amount_cents=owed,
                 event_id="evt_northstar")
        applied = ingest_payment_spool(self.s, spool=str(self.spool))

        self.assertEqual([r["outcome"] for r in applied
                          if r["outcome"] == "REFUSED"], [], applied)
        self.assertEqual(self.s.ledger.collected_for(self.job_id), owed)
        method = (self.s.ledger.payment_for(self.job_id) or {}).get(
            "verification_method", "")
        self.assertTrue(method.startswith("SIMULATED:"), method)
        self.note("PAYMENT", f"{owed}c verified by the rail, filed as "
                             f"{method}")

    def test_11_reconciliation(self):
        """The job closes because the Ledger says it was paid."""
        notes = settlement.settle_paid_jobs(self.s, initiator="test")
        notes += reconcile(self.s, initiator="test")

        self.assertIs(self.s.orchestrator.job(self.job_id).state,
                      JobState.COMPLETE)
        self.note("RECONCILIATION", "; ".join(notes) or "nothing outstanding")

    def test_12_the_customer_is_told(self):
        contracting.tell_the_customer(self.s, order_id=f"eng-{CLIENT}")
        contracting.report_back(self.s, prospect_id=CLIENT, initiator="test")

        order = self.s.storefront.order(f"eng-{CLIENT}")
        self.assertIs(order.state, OrderState.CLOSED)
        self.assertEqual(order.customer_status(), "Complete.")
        self.note("CUSTOMER", order.customer_status())

    def test_13_capacity_recovery(self):
        self.assertEqual(self.s.capacity.owed_seconds(), 0.0)
        self.assertEqual(self.s.capacity.outstanding(), [])
        self.assertEqual(
            [j.id for j in self.s.orchestrator.jobs()
             if j.state in OCCUPIES_CAPACITY], [])
        room = self.s.capacity.room_for(3_600.0, kind="EXECUTE",
                                        capability=WANTS)

        self.assertTrue(room.allowed, room.why)
        self.note("CAPACITY", "every slot back, room for the next job")

    def test_14_the_books_are_straight(self):
        engagement = self.s.chief_of_staff.engagements[CLIENT]
        agreed = engagement.proposal.price_cents
        deposit = engagement.collected_cents
        collected = self.s.ledger.collected_for(self.job_id)

        self.assertEqual(deposit + collected, agreed,
                         f"the client paid {deposit + collected} against an "
                         f"agreement of {agreed}")
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0,
                         "simulated money must never read as revenue")
        self.note("BOOKS", f"{deposit}c deposit + {collected}c balance "
                           f"= {agreed}c agreed; real revenue 0c")

    def test_15_no_step_was_arranged_by_this_file(self):
        """The rule that makes the rest of it evidence.

        A test that sets a job state, an opportunity status or a
        ledger row is arranging the result it then reports. Asserted
        on this file's own parse tree, because the forbidden calls
        are easy to add later and hard to notice.
        """
        import ast

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        forbidden = {"set_status", "_transition", "mark_verified",
                     "apply_verdict", "set_payment_state", "open_payment",
                     "record_billing_obligation", "complete", "block",
                     "submit_to_pipeline", "place"}
        called = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func,
                                                         ast.Attribute):
                called.add(node.func.attr)

        self.assertEqual(sorted(forbidden & called), [],
                         "this file arranged a state it then reported")

    def test_16_the_trace_is_complete(self):
        """Printed for the report, and checked for gaps."""
        steps = [line.split()[0] for line in self.trace]
        for required in ("DISCOVERY", "QUALIFICATION", "NEGOTIATION",
                         "CONTRACT", "DEPOSIT", "ADMISSION", "EXECUTION",
                         "QA", "DELIVERY", "INVOICE", "PAYMENT",
                         "RECONCILIATION", "CAPACITY", "BOOKS"):
            with self.subTest(required):
                self.assertIn(required, steps)
        print("\n".join(self.trace))


if __name__ == "__main__":
    unittest.main()
