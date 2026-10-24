"""What the fictional clients do to DeskPilot, day by day.

:mod:`simulation.customers` says who they are. This says what they do,
and it is kept separate because the split is load-bearing: nothing in
``customers`` holds a reference to a ``Solvent``, so the model of how
a client behaves cannot quietly start reading DeskPilot's state and
reacting to it. A client who adapts to the system under test is a
client who flatters it.

**Every act goes through a production API.** An order is placed with
``Storefront.place``; a counteroffer is ``Negotiation.respond``; a
deposit is ``Engagement.record_payment`` with a verified event; a
file is ``Engagement.supply``. There is no back door and nothing here
sets a state DeskPilot owns.

**Two acts have no production counterpart, and both are marked.**
:meth:`World._the_outside_world` wraps them, and the name is
deliberately greppable:

* **Putting a prospect in the CRM.** ``CRM.discover`` has no caller
  anywhere in ``solvent/``, and no CLI command or web route reaches
  it. The relationship layer -- CRM, negotiation, engagement, three
  scheduled tasks -- sits behind a door the product cannot open. In
  reality a person does it, or a prospecting run does, and
  DeskPilot's prospecting module says of itself that it is
  EXPERIMENTAL and deliberately not wired into commercial operation.
* **Accepting an opportunity.** ``OpportunityStatus.ACCEPTED`` is set
  only by ``qualification.select``, which also mints a job and
  applies the Governor's verdict; for a negotiated enquiry there is
  nothing to execute that job, and a probe confirms such a job is
  invisible to supervision for as long as you care to wait.

Those two are the simulation standing in for the world, not DeskPilot
working. A report that counted them as DeskPilot's would be claiming
an integration that does not exist.

**No message is ever sent.** A client "replying" here means their
standing moves to REPLIED, which is what DeskPilot would record on
reading an email it never sends. ``egress.simulation_only`` stays on
throughout, and the audit hook would refuse a socket in any case.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from solvent import contracting
from solvent.crm import Basis, Contact, Evidence, Standing
from solvent.discovery import OpportunityStatus
from solvent.engagement import Acceptance, Stage
from solvent.errors import FailClosed
from solvent.negotiation import Outcome
from solvent.types import JobState

from simulation import rail
from simulation.customers import Population

#: Written into evidence and contact records so a reader of the CRM
#: can see at once that none of it came from a real company.
FICTIONAL = "SIMULATION: a fictional client, invented for a run"


@dataclass
class Act:
    """One thing a client did, or tried to and could not."""

    day: int
    customer_id: str
    act: str
    done: bool
    detail: str = ""

    def line(self) -> str:
        mark = "" if self.done else "refused: "
        return f"day {self.day} {self.customer_id} {self.act}: {mark}{self.detail}"


@dataclass
class World:
    """The outside world, acting on one DeskPilot once per day."""

    solvent: object
    population: Population
    workroot: pathlib.Path
    #: Where a simulated payment provider drops its signed webhook
    #: deliveries. The production ``payments`` task reads it, the
    #: production rail verifies the signature and the production
    #: Ledger decides what it means. Empty means nobody pays, which
    #: is a run worth being able to ask for.
    spool: pathlib.Path | None = None
    #: Everything that happened, for the audit to read afterwards.
    log: list = field(default_factory=list)
    #: How many acts have no production counterpart. Counted rather
    #: than hidden, so a report can state the figure instead of a
    #: reader having to take the docstring on trust.
    unbacked_acts: int = 0
    #: The day a client first saw something, keyed by
    #: ``(customer_id, what)``. Kept here rather than read off the
    #: engagement's own history, because that history is stamped with
    #: the **real** wall clock -- ``engagement._now()`` is
    #: ``datetime.now``, and in a run dated January it would put every
    #: transition months in the future. A client knows when they got
    #: their invoice; this is that knowledge.
    noticed: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.workroot = pathlib.Path(self.workroot)

    # ---- bookkeeping ---------------------------------------------------

    def _did(self, customer, day: int, act: str, detail: str = "") -> Act:
        entry = Act(day=day, customer_id=customer.customer_id, act=act,
                    done=True, detail=detail)
        customer.note(day, act, detail)
        self.log.append(entry)
        return entry

    def _could_not(self, customer, day: int, act: str, why: str) -> Act:
        entry = Act(day=day, customer_id=customer.customer_id, act=act,
                    done=False, detail=why)
        self.log.append(entry)
        return entry

    def _the_outside_world(self, customer, day: int, act: str,
                           detail: str) -> Act:
        """An act DeskPilot has no production path for. Counted, not hidden."""
        self.unbacked_acts += 1
        return self._did(customer, day, f"[outside-world] {act}", detail)

    def _draw(self, customer, day: int, act: str):
        return self.population.stream(customer.customer_id, day, act)

    def _since(self, customer, day: int, what: str) -> int:
        """Working days since the client first saw ``what``, recording it."""
        key = (customer.customer_id, what)
        first = self.noticed.setdefault(key, day)
        return day - first

    # ---- the day -------------------------------------------------------

    def open_for_business(self, day) -> list:
        """Everything the clients do on this day, in order.

        ``day`` is a :class:`simulation.calendar.Day`. On a day the
        clients do not work, only the acts that are not theirs to
        choose happen -- which is none of them, so the method returns
        at once. DeskPilot's own scheduler still ticks: an unattended
        system does not take Saturdays off, and a client's deposit
        clearing on a Sunday is the bank's business, not theirs.
        """
        if not day.working:
            return []
        acts: list = []
        for step in (self._arrive, self._go_quiet, self._haggle, self._sign,
                     self._pay_the_deposit, self._send_the_file,
                     self._ask_for_changes, self._pay_the_invoice,
                     self._come_back):
            acts.extend(step(day))
        return acts

    # ---- 1. arriving ---------------------------------------------------

    def _arrive(self, day) -> list:
        out = []
        for customer in self.population.customers:
            if customer.arrives_on != day.index:
                continue
            if customer.door == "order":
                out.append(self._place_an_order(customer, day))
            else:
                out.append(self._write_in(customer, day))
        return [a for a in out if a is not None]

    def _place_an_order(self, customer, day, nth: int = 1):
        """The inbound door. Entirely production from here on."""
        offer = _offer_for(customer.wants)
        order_id = f"ord-{customer.customer_id}-{nth}"
        answers = self.population.materials_for(
            customer, workroot=self.workroot / "materials", nth=nth)
        try:
            order = self.solvent.storefront.place(
                order_id=order_id, offer_id=offer.offer_id,
                client_ref=customer.prospect_id, answers=answers,
                offered_cents=customer.budget_cents)
        except FailClosed as refusal:
            return self._could_not(customer, day.index, "place an order",
                                   str(refusal)[:160])
        if order.state.value != "RECEIVED":
            # The storefront turned them away -- too cheap for the
            # owner's floor, usually. A real refusal and a real
            # outcome: this client never becomes revenue.
            return self._could_not(customer, day.index, "place an order",
                                   f"{order.state.value}: "
                                   f"{order.why or ', '.join(order.missing)}")
        return self._did(customer, day.index, "place an order",
                         f"{offer.offer_id} at {customer.budget_cents}c")

    def _write_in(self, customer, day):
        """The relationship door, and the two gaps it runs into.

        A client emails asking for a price. DeskPilot has nowhere to
        put that: no inbound channel creates a CRM prospect, and no
        route produces an ACCEPTED opportunity without also minting a
        job. Both are done here and both are marked.
        """
        crm = self.solvent.crm
        # The id the CRM hands back, not the one asked for. It
        # deduplicates on (company, state) and returns the company it
        # already holds, so taking the asked-for id on trust raised
        # "no prospect" the first time two fictional clients happened
        # to share a name.
        held = crm.discover(prospect_id=customer.prospect_id,
                            company=customer.company,
                            industry=customer.industry, state=customer.state)
        if held.prospect_id != customer.prospect_id:
            return self._could_not(
                customer, day.index, "write in asking for a price",
                f"the CRM already holds {held.company} in {customer.state} as "
                f"{held.prospect_id}")
        crm.add_evidence(customer.prospect_id, Evidence(
            claim=f"they asked for a price for {customer.wants}",
            basis=Basis.CLIENT_STATED, source=FICTIONAL,
            seen_on=day.date.isoformat()))
        crm.add_contact(customer.prospect_id, Contact(
            name="Ops", email=customer.email, role="operations",
            source=FICTIONAL, seen_on=day.date.isoformat()))
        crm.advance(customer.prospect_id, Standing.QUALIFIED,
                    "they named a skill DeskPilot sells")
        crm.advance(customer.prospect_id, Standing.CONTACTED,
                    "we answered their enquiry")
        crm.advance(customer.prospect_id, Standing.REPLIED,
                    "they asked what it would cost")

        self.solvent.discovery.insert(
            "inbox", owner_identity=self.owner_identity(), postings=[{
                "ref": f"enq-{customer.customer_id}",
                "title": f"{customer.wants} for {customer.company}",
                "quoted_cents": customer.budget_cents,
                "needs": [customer.wants],
                "client_ref": customer.prospect_id,
                "project_state": customer.state,
                "client_state": customer.state,
                "body": FICTIONAL}])
        row = next((r for r in self.solvent.discovery.opportunities()
                    if r.get("client_ref") == customer.prospect_id), None)
        if row is not None:
            self.solvent.discovery.set_status(str(row["id"]),
                                              OpportunityStatus.ACCEPTED)
        return self._the_outside_world(
            customer, day.index, "write in asking for a price",
            "CRM.discover and OpportunityStatus.ACCEPTED have no production "
            "caller; the simulation stands in for the world here")

    def owner_identity(self) -> str:
        from solvent.workorder import owner_identity_of

        return owner_identity_of(self.solvent.policy)

    # ---- 2. going quiet -------------------------------------------------

    def _go_quiet(self, day) -> list:
        """The commonest way a deal dies: nothing at all.

        Checked before the other acts, so a client who goes quiet
        today does not also haggle today.

        Only the uncommitted. Somebody who has signed does not
        vanish; they decline, or they pay late, and both of those are
        modelled elsewhere. Somebody who has already paid once is a
        customer, and losing them is churn --
        :meth:`_come_back`'s absence rather than this.
        """
        out = []
        chief = getattr(self.solvent, "chief_of_staff", None)
        for customer in self.population.active(day.index):
            if customer.settled or customer.arrives_on == day.index:
                continue
            signed = chief is not None and (
                getattr(chief.engagements.get(customer.prospect_id), "acceptance",
                        None) is not None)
            if signed:
                continue
            if self._draw(customer, day.index, "quiet").random() \
                    >= customer.temperament.goes_quiet:
                continue
            customer.quiet_since = day.index
            out.append(self._did(customer, day.index, "go quiet",
                                 "stopped answering"))
        return out

    # ---- 3. haggling ----------------------------------------------------

    def _haggle(self, day) -> list:
        """Answer an open negotiation: counter, accept, or walk away."""
        out = []
        chief = getattr(self.solvent, "chief_of_staff", None)
        if chief is None:
            return out
        for customer in self.population.active(day.index):
            deal = chief.negotiations.get(customer.prospect_id)
            if deal is None or deal.outcome is not Outcome.OPEN:
                continue
            asking = deal.current_cents
            if deal.floor > customer.budget_cents:
                # No price exists that both sides can live with. The
                # client says so and stops; this is a lost deal, not
                # a defect, and the run needs some of them.
                customer.quiet_since = day.index
                out.append(self._did(
                    customer, day.index, "walk away",
                    f"floor {deal.floor}c is above their {customer.budget_cents}c"))
                continue
            if asking <= customer.budget_cents:
                deal.respond(asking, why="the client accepted the price")
                out.append(self._did(customer, day.index, "accept the price",
                                     f"{asking}c"))
                continue
            offer = max(int(asking * customer.temperament.counters_at),
                        deal.floor)
            offer = min(offer, customer.budget_cents)
            deal.respond(offer, why="the client countered")
            out.append(self._did(customer, day.index, "counter",
                                 f"{offer}c against {asking}c"))
        return out

    # ---- 4. signing -----------------------------------------------------

    def _sign(self, day) -> list:
        out = []
        chief = getattr(self.solvent, "chief_of_staff", None)
        if chief is None:
            return out
        for customer in self.population.active(day.index):
            deal = chief.engagements.get(customer.prospect_id)
            if deal is None or deal.stage is not Stage.SENT:
                continue
            if deal.proposal.price_cents > customer.budget_cents:
                deal.declined("above what they said they could spend")
                customer.quiet_since = day.index
                out.append(self._did(customer, day.index, "decline",
                                     f"{deal.proposal.price_cents}c"))
                continue
            deal.accept(Acceptance(
                accepted_by=f"Ops at {customer.company}",
                accepted_on=day.date.isoformat(),
                method="reply to the proposal",
                terms_digest=f"sha256:{customer.customer_id}"))
            out.append(self._did(customer, day.index, "sign",
                                 f"{deal.proposal.price_cents}c"))
        return out

    # ---- 5. the deposit --------------------------------------------------

    def _pay_the_deposit(self, day) -> list:
        """Only a verified event moves anything. There is no other path."""
        out = []
        chief = getattr(self.solvent, "chief_of_staff", None)
        if chief is None:
            return out
        for customer in self.population.active(day.index):
            deal = chief.engagements.get(customer.prospect_id)
            if deal is None or deal.acceptance is None:
                continue
            if deal.deposit_satisfied or deal.stage.terminal:
                continue
            if deal.stage is Stage.ACCEPTED:
                # Nobody has asked them for it yet. The ask is
                # DeskPilot's move, and the client waits for it.
                deal.request_deposit()
            if self._since(customer, day.index, "asked for a deposit") \
                    < customer.temperament.deposit_takes_days:
                continue
            deal.record_payment({
                "verified": True, "cents": deal.deposit_due_cents,
                "external_ref": f"sim_dep_{customer.customer_id}_{deal.engagement_id}"})
            out.append(self._did(customer, day.index, "pay the deposit",
                                 f"{deal.deposit_due_cents}c"))
        return out

    # ---- 6. the file ------------------------------------------------------

    def _send_the_file(self, day) -> list:
        """Sent once the deposit has cleared, which is when a client does."""
        out = []
        chief = getattr(self.solvent, "chief_of_staff", None)
        if chief is None:
            return out
        for customer in self.population.active(day.index):
            deal = chief.engagements.get(customer.prospect_id)
            if deal is None or not deal.work_authorised or deal.materials:
                continue
            nth = customer.settled + 1
            answers = self.population.materials_for(
                customer, workroot=self.workroot / "materials", nth=nth)
            deal.supply(**answers)
            out.append(self._did(customer, day.index, "send the file",
                                 answers["source_file"].rsplit("/", 1)[-1]))
        return out

    # ---- 7. revisions ------------------------------------------------------

    def _ask_for_changes(self, day) -> list:
        """Send a complaint through the production intake.

        ``ClientRelations.handle`` is the real door: it receives the
        text as untrusted content, classifies it, investigates it
        against the committed requirements and the delivered
        artifact, and drafts a reply. Nothing here decides whether
        the client is right -- that is the point of the module.

        Asked once per job. A client who complains every day is a
        different simulation.
        """
        out = []
        chief = getattr(self.solvent, "chief_of_staff", None)
        relations = getattr(self.solvent, "relations", None)
        if chief is None or relations is None:
            return out
        for customer in self.population.active(day.index):
            deal = chief.engagements.get(customer.prospect_id)
            if deal is None or deal.stage not in (Stage.DELIVERED,
                                                  Stage.INVOICED):
                continue
            key = (customer.customer_id, f"complained-{customer.settled}")
            if key in self.noticed:
                continue
            if self._draw(customer, day.index, "revision").random() \
                    >= customer.temperament.asks_for_changes:
                continue
            job_id = _job_of(self.solvent, f"eng-{customer.prospect_id}")
            if not job_id:
                continue
            self.noticed[key] = day.index
            handled = relations.handle(
                job_id=job_id, source=customer.email,
                body="This is not what we asked for. Please look at it again.")
            out.append(self._did(
                customer, day.index, "ask for changes",
                getattr(handled, "classification", "") or "complaint received"))
        return out

    # ---- 8. paying the invoice ----------------------------------------------

    def _pay_the_invoice(self, day) -> list:
        """Pay what DeskPilot billed, through the real payment path.

        The client writes a signed webhook delivery into the spool.
        Nothing else: the ``payments`` task reads it, the rail checks
        the signature, and the Ledger decides what it means. A client
        who waits longer than their temperament says simply has not
        paid yet, and the shop's unpaid exposure grows -- which is
        the thing the Governor's ceiling is for and therefore worth
        a run actually producing.

        **They pay what they are billed.** The invoice is the
        Ledger's figure, not the engagement's: an engagement-backed
        job is billed its full price even though a deposit has
        already been taken, and a simulation that quietly paid the
        balance instead would hide that. See the audit, which
        compares the two.
        """
        out = []
        if self.spool is None:
            return out
        ledger = self.solvent.ledger
        for job in self.solvent.orchestrator.jobs():
            if job.state is not JobState.AWAITING_PAYMENT:
                continue
            customer = self._customer_of(job.client_id)
            if customer is None or customer.quiet_since is not None:
                continue
            key = (customer.customer_id, f"invoiced-{job.id}")
            if key in self.noticed:
                waited = day.index - self.noticed[key]
            else:
                self.noticed[key] = day.index
                waited = 0
            if waited < customer.temperament.balance_takes_days:
                continue
            if (customer.customer_id, f"paid-{job.id}") in self.noticed:
                continue
            owed = int((ledger.payment_for(job.id) or {}).get("amount_cents") or 0)
            if owed <= 0:
                continue
            self.noticed[(customer.customer_id, f"paid-{job.id}")] = day.index
            rail.pay(self.spool, job_id=job.id, amount_cents=owed,
                     event_id=f"evt_{job.id}")
            customer.settled += 1
            out.append(self._did(customer, day.index, "pay the invoice",
                                 f"{owed}c for {job.id}"))
        return out

    def _customer_of(self, client_ref: str):
        prefix = "client:"
        if not str(client_ref).startswith(prefix):
            return None
        return self.population.by_id().get(str(client_ref)[len(prefix):])

    # ---- 9. coming back -----------------------------------------------------

    def _come_back(self, day) -> list:
        """Repeat work, through the inbound door.

        A client who has already paid once orders rather than
        negotiates: they know the price. This is where repeat revenue
        comes from, and its absence is churn.
        """
        out = []
        for customer in self.population.active(day.index):
            if not customer.settled:
                continue
            placed = sum(1 for h in customer.history
                         if h["what"] == "place an order")
            if placed >= customer.settled + 1:
                continue
            if self._draw(customer, day.index, "return").random() \
                    >= customer.temperament.comes_back:
                continue
            act = self._place_an_order(customer, day, nth=customer.settled + 1)
            if act is not None:
                out.append(act)
        return out


# ------------------------------------------------------------------ helpers


def _offer_for(capability: str):
    from solvent import storefront as shop

    found = [o for o in shop.OFFERS if o.capability == capability]
    if len(found) != 1:
        raise FailClosed(
            f"{len(found)} offers sell {capability!r}; the simulation does "
            "not get to choose which terms a client is sold")
    return found[0]


def _job_of(solvent, order_id: str) -> str:
    """The job the pipeline ran for this order.

    Asks ``contracting.job_for``, which already follows the order ->
    opportunity -> job link through the records. A second copy of
    that walk here would be a second thing to drift.
    """
    return contracting.job_for(solvent, order_id)
