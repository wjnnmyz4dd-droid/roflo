"""The inbound channel: customers finding DeskPilot, rather than the reverse.

Two revenue channels, one pipeline. That second half is the whole design
constraint. An order placed through a storefront and an opportunity found on
a board are different ways of *learning about* work, and the same thing
afterwards: both must be qualified, priced, approved, executed, verified,
delivered, invoiced and reconciled by the machinery that already exists.

So an order does not get its own execution path. It becomes an
``Opportunity`` on a ``StorefrontSource``, which is an ordinary
:class:`~solvent.discovery.WorkSource`, and enters through the same door as
everything else. That is not a stylistic preference: a second engine would
need its own qualification, its own Governor call, its own verification tier
and its own audit trail, and the day those drifted apart is the day one of
them started taking work the other would have refused.

**Nothing here is published.** An offer is a draft: a description, a scope,
a price and an intake form, held locally and validated against what DeskPilot
can actually do. Putting it in front of real customers means creating an
account on somebody's platform and advertising to the public, which is the
owner's decision and nobody else's. ``published`` is False and there is no
code here that can change it.

**Nothing may be advertised that is not certified.** Every offer names a
capability, and an offer whose capability is not cleared for deployment
cannot be opened. An advert is a promise, and the one thing worse than not
finding customers is finding one for work that cannot be delivered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

from . import sourceaccess as access
from .content import quarantine
from .discovery import Opportunity, WorkSource
from .errors import FailClosed
from .types import Cents, PrivacyClass


class OrderState(Enum):
    """Where a customer's order has got to, in words a customer can read."""

    #: The form was submitted and nothing has been promised.
    RECEIVED = "RECEIVED"
    #: Something needed is missing; the customer has been asked.
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    #: Handed to the pipeline as an opportunity. Not yet accepted.
    IN_QUALIFICATION = "IN_QUALIFICATION"
    #: The pipeline declined it, with a reason.
    DECLINED = "DECLINED"
    #: Running.
    IN_PROGRESS = "IN_PROGRESS"
    #: Verified and delivered.
    DELIVERED = "DELIVERED"
    #: The customer asked for a change within the revision policy.
    IN_REVISION = "IN_REVISION"
    #: Paid and reconciled.
    CLOSED = "CLOSED"


#: States from which an order is not going anywhere without a person.
TERMINAL = (OrderState.DECLINED, OrderState.CLOSED)


@dataclass(frozen=True, slots=True)
class IntakeQuestion:
    """One thing a customer must tell us before work can start.

    ``required`` is not decoration. A question marked required and left
    blank stops the order at NEEDS_INFORMATION rather than starting work on
    a guess, which is the most expensive way to discover an assumption.
    """

    key: str
    prompt: str
    required: bool = True
    #: A short note on why it is asked, shown to the customer. People answer
    #: better when they know what the answer is for.
    why: str = ""


@dataclass(frozen=True, slots=True)
class RevisionPolicy:
    """What a customer may ask for after delivery, and what they may not."""

    included: int = 1
    #: What counts as a revision rather than new work.
    covers: str = ("corrections to anything that does not match the agreed "
                   "requirements")
    #: What does not, stated up front rather than argued about later.
    excludes: str = ("changes to the requirements themselves, which are a "
                     "new order and priced as one")
    window_days: int = 7


@dataclass(frozen=True, slots=True)
class OfferTerm:
    """One verifiable promise an offer makes, as a named check.

    This is the difference between an offer a person must negotiate and
    one DeskPilot can execute on its own. ``run_business_cycle`` refuses
    to infer a client's requirements from a posting -- prose is not a
    contract, and a deliverable verified against terms DeskPilot wrote
    for itself is verified against its own invention. So the terms have
    to come from somewhere real.

    A published offer is that somewhere. It is a standing contract: the
    scope is fixed, the customer reads it before ordering, and ordering
    is the agreement. The named ``check`` is what actually runs against
    the deliverable, so what the advert promises and what the checker
    recomputes are the same sentence.

    ``check`` must name a check the capability genuinely registers.
    :func:`offer_problem` refuses an offer that promises one it does
    not, because an unverifiable promise is the one kind of promise this
    system must not be able to publish.
    """

    check: str
    acceptance: str
    params: dict = field(default_factory=dict)
    #: The intake question whose answer supplies this term's parameters.
    #:
    #: Some checks both instruct and verify -- ``map_fields`` says which
    #: source column fills which target field *and* checks that it did.
    #: A fixed offer cannot know the customer's column names, so for
    #: those the parameters come from the customer's own answer rather
    #: than from the advert. The answer must be structured: a form field
    #: with named keys, not a sentence, because turning a sentence into
    #: a mapping is interpretation and the five skills call no model to
    #: do it with.
    params_from: str = ""


@dataclass(frozen=True, slots=True)
class ServiceOffer:
    """One thing DeskPilot will do for money, described well enough to sell.

    Every field exists because leaving it out causes a specific argument
    with a customer later: what exactly is included, what it costs, what
    arrives, how fast, and what happens if it is wrong.
    """

    offer_id: str
    capability: str
    title: str
    summary: str
    #: Bullet points. What the customer gets.
    scope_included: tuple[str, ...]
    #: Bullet points. What they do not, said plainly rather than implied.
    scope_excluded: tuple[str, ...]
    deliverables: tuple[str, ...]
    intake: tuple[IntakeQuestion, ...]
    #: The smallest price this offer may be sold at, in cents.
    minimum_cents: Cents
    #: What the customer is told about timing. Deliberately conservative and
    #: never "instant": the work is fast, but the owner's approval is not.
    turnaround: str
    revisions: RevisionPolicy = field(default_factory=RevisionPolicy)
    #: Disclosures the customer reads before ordering.
    terms: tuple[str, ...] = ()
    #: An offer is a draft until a person publishes it somewhere real.
    published: bool = False
    #: What the deliverable is checked against. An offer with none cannot
    #: be sold: see :func:`offer_problem`.
    requirements: tuple[OfferTerm, ...] = ()

    def intake_keys(self) -> tuple[str, ...]:
        return tuple(q.key for q in self.intake)

    def missing_answers(self, answers: dict) -> tuple[str, ...]:
        return tuple(q.key for q in self.intake
                     if q.required and not str(answers.get(q.key, "")).strip())

    def to_dict(self) -> dict:
        return {
            "offer_id": self.offer_id, "capability": self.capability,
            "title": self.title, "summary": self.summary,
            "scope_included": list(self.scope_included),
            "scope_excluded": list(self.scope_excluded),
            "deliverables": list(self.deliverables),
            "intake": [{"key": q.key, "prompt": q.prompt,
                        "required": q.required, "why": q.why}
                       for q in self.intake],
            "minimum_cents": self.minimum_cents,
            "turnaround": self.turnaround,
            "revisions": {"included": self.revisions.included,
                          "covers": self.revisions.covers,
                          "excludes": self.revisions.excludes,
                          "window_days": self.revisions.window_days},
            "terms": list(self.terms),
            "published": self.published,
            "requirements": [{"check": r.check, "acceptance": r.acceptance,
                              "params": dict(r.params)}
                             for r in self.requirements],
        }


# --------------------------------------------------------------- the terms

#: Said on every offer, because every one of them is a thing a customer
#: would otherwise be surprised by.
COMMON_TERMS = (
    "The price is agreed before work begins. A fixed-price order is that "
    "price whatever the work turns out to take.",
    "Work starts once the order is approved. Nothing is charged before then.",
    "Every deliverable is checked against your stated requirements before it "
    "is sent, and the check is recomputed from your own file.",
    "Your files are used to do your job and nothing else. They are not sent "
    "to any third party without your agreement.",
    "If your requirements turn out to be ambiguous, you will be asked rather "
    "than guessed at. That may delay delivery and will not change the price.",
)


def _q(key, prompt, why="", required=True):
    return IntakeQuestion(key=key, prompt=prompt, why=why, required=required)


#: The file every one of these services needs, asked the same way each time.
_SOURCE_FILE = _q("source_file", "Which file should we work on?",
                  "Without it there is nothing to do, and we will not start "
                  "an order we cannot finish.")
_DEADLINE = _q("needed_by", "When do you need it back?",
               "So we can tell you now if that is not realistic, rather "
               "than later.", required=False)


def _term(check, acceptance, params_from="", **params):
    return OfferTerm(check=check, acceptance=acceptance, params=params,
                     params_from=params_from)


#: The promise every one of these offers makes, because a deliverable
#: nobody can open is not a deliverable. Each skill's own checker names
#: this differently, so the per-offer lists below say which.
def _offer(offer_id, capability, title, summary, included, excluded,
           deliverables, extra_questions, minimum_cents, turnaround,
           requirements=()):
    return ServiceOffer(
        offer_id=offer_id, capability=capability, title=title, summary=summary,
        scope_included=included, scope_excluded=excluded,
        deliverables=deliverables,
        intake=(_SOURCE_FILE,) + tuple(extra_questions) + (_DEADLINE,),
        minimum_cents=minimum_cents, turnaround=turnaround,
        terms=COMMON_TERMS, requirements=tuple(requirements))


def offer_problem(service: "ServiceOffer") -> str:
    """Why this offer may not be sold, or ``""``.

    Two conditions, both fail-closed. An offer that promises nothing
    checkable cannot be verified, so selling it would mean delivering
    against terms nobody could recompute. And an offer naming a check
    its capability does not register would fail at the verification
    stage of every order placed against it -- after the customer has
    paid, which is the worst possible moment to discover it.

    Checked here rather than at delivery because an advert is a promise
    and the time to refuse an impossible one is before it is shown.
    """
    from . import checks

    if not service.requirements:
        return (f"offer {service.offer_id!r} promises nothing that can be "
                "checked, so a deliverable against it could not be verified")
    known = checks.checks_for(service.capability)
    if not known:
        return (f"{service.capability!r} registers no checks at all, so "
                f"offer {service.offer_id!r} cannot be verified")
    unknown = sorted({r.check for r in service.requirements} - set(known))
    if unknown:
        return (f"offer {service.offer_id!r} promises {', '.join(unknown)}, "
                f"which {service.capability!r} does not register; the order "
                "would fail verification after the customer had paid")
    return ""


# ------------------------------------------------------------- the offers
#
# One per certified skill, and no more. The temptation is to advertise
# adjacent things the skill nearly does -- "and light data analysis" -- and
# that is how an order arrives for work nobody can verify.

CSV_CLEANUP = _offer(
    "csv-cleanup", "csv-cleanup",
    "Spreadsheet and CSV cleanup",
    "Duplicates removed, whitespace trimmed, columns renamed and reordered, "
    "rows sorted and filtered -- against rules you state, and checked "
    "against your own file before it comes back.",
    included=(
        "Remove exact duplicate rows, or duplicates by the columns you name",
        "Trim leading and trailing whitespace",
        "Rename, reorder, drop and keep columns",
        "Sort and filter rows by your rules",
        "Normalise dates and numbers to one stated format",
        "A written report of what changed and how many rows it affected"),
    excluded=(
        "Deciding which rows are duplicates when you have not said how",
        "Inventing missing values, or correcting data we cannot check",
        "Interpreting what the data means"),
    deliverables=("The cleaned file in the same format you sent",
                  "A change report listing every rule applied"),
    extra_questions=(
        _q("rules", "What should be cleaned up, and by what rule?",
           "Precise rules are what makes the result checkable. 'Remove "
           "duplicate rows where Email matches' is a rule; 'tidy it up' is "
           "not, and we would have to come back and ask."),
        _q("duplicate_key", "If removing duplicates, which columns identify "
           "a duplicate?", "Two rows for the same person with different "
           "phone numbers may or may not be a duplicate. You decide.",
           required=False)),
    minimum_cents=4_000,
    turnaround="Same working day once the order is approved",
    requirements=(
        _term("parses_as_csv", "the cleaned file opens as CSV"),
        _term("trim_whitespace", "no leading or trailing spaces remain"),
        _term("drop_exact_duplicates", "rows that repeat exactly appear once"),
        # Stated with the same assumption the line above makes. Promising
        # reconciliation while leaving `duplicates_removed` at its default
        # without also promising the dedupe is a contradiction the checker
        # catches at verification -- which is to say, after the work is
        # done. The two terms are written together for that reason.
        _term("row_reconciliation",
              "every input row is accounted for, less exact duplicates",
              duplicates_removed=True),
    ),
)

SPREADSHEET_AUTOMATION = _offer(
    "spreadsheet-automation", "spreadsheet-automation",
    "Spreadsheet formatting and calculation",
    "Computed columns, totals, formatting and multi-sheet workbooks, built "
    "from rules you state and recomputed from your source before delivery.",
    included=(
        "Computed columns -- products, sums, differences, percentages",
        "Total and subtotal rows",
        "Number, date and currency formatting",
        "Column widths, headers and freeze panes",
        "Splitting one sheet into several, or combining several into one"),
    excluded=(
        "Macros or VBA",
        "Live links to other workbooks or external data",
        "Financial modelling or forecasting, which is advice rather than "
        "calculation"),
    deliverables=("The finished .xlsx workbook",
                  "A note of every formula and format applied"),
    extra_questions=(
        _q("calculations", "Which calculations do you need, and from which "
           "columns?",
           "Stated as arithmetic we can recompute: 'Amount = Units x Rate'."),
        _q("formatting", "Any formatting requirements?",
           "Currency symbols, decimal places, date format.",
           required=False)),
    minimum_cents=4_000,
    turnaround="Same working day once the order is approved",
    requirements=(
        _term("opens_as_workbook", "the workbook opens"),
        # Before the calculation, because a calculation naming a column
        # the rename has not yet produced fails at execution with "there
        # is no column named ...". The order of these terms is the order
        # the work happens in.
        _term("rename_headers",
              "your headers are renamed as you asked",
              params_from="formatting"),
        _term("compute_column",
              "the column you asked for is computed from your own figures",
              params_from="calculations"),
        _term("row_reconciliation", "every input row is accounted for"),
        _term("formulas_agree",
              "every computed cell recomputes to the value shown"),
    ),
)

DOCUMENT_PROCESSING = _offer(
    "document-processing", "document-processing",
    "Document preparation from a template",
    "Templates filled from your data, converted between formats, and checked "
    "field by field against what you supplied.",
    included=(
        "Fill a template from a spreadsheet or a list of values",
        "Produce one document per row, or one combined document",
        "Convert between .docx, .md and plain text",
        "Consistent headings, numbering and styles"),
    excluded=(
        "Writing the content itself",
        "Legal, medical or financial drafting of any kind",
        "Filling a field you have not supplied a value for -- it will be "
        "flagged, never invented"),
    deliverables=("The completed documents",
                  "A list of every field filled and its source value"),
    extra_questions=(
        _q("template", "Which template should we fill?",
           "Send the template as well as the data."),
        _q("missing_values", "What should happen to rows with missing "
           "values?", "Skip the row, leave the field blank, or stop and ask. "
           "The one thing we will not do is make something up.")),
    minimum_cents=4_000,
    turnaround="Same working day once the order is approved",
    requirements=(
        _term("document_opens", "the document opens"),
        _term("extract_fields",
              "the fields you named are pulled out of your document",
              params_from="template"),
        _term("no_invented_content",
              "nothing appears that was not in your source"),
        _term("absences_disclosed",
              "anything missing from your source is listed rather than "
              "filled in"),
    ),
)

DATA_ENTRY = _offer(
    "data-entry", "data-entry",
    "Structured data entry and transcription",
    "Data moved from one structured form into another, field by field, with "
    "every value traceable to where it came from.",
    included=(
        "Transfer data between spreadsheets, CSVs and structured documents",
        "Map fields between differing layouts",
        "Validate values against a list or a format you supply",
        "Flag anything that does not match"),
    excluded=(
        "Reading handwriting or scanned images",
        "Transcribing audio or video",
        "Judgement calls about which value is correct when two disagree"),
    deliverables=("The populated file",
                  "An exceptions list of everything that could not be "
                  "mapped cleanly"),
    extra_questions=(
        _q("field_mapping", "Which field goes where?",
           "A mapping from the source columns to the destination ones."),
        _q("validation", "Should any field be validated against a list or "
           "format?", "Postcodes, country codes, product codes.",
           required=False)),
    minimum_cents=4_000,
    turnaround="Same working day once the order is approved",
    requirements=(
        _term("map_fields",
              "each of your source columns lands in the field you named",
              params_from="field_mapping"),
        _term("parses_as_delivered", "the file opens in the format agreed"),
        _term("records_reconcile", "every source record is accounted for"),
        _term("no_invented_values",
              "no value appears that was not in your source"),
    ),
)

REPORT_BUILDER = _offer(
    "report-builder", "report-builder",
    "Business report preparation",
    "A structured report assembled from your data -- summaries, groupings "
    "and totals, each figure recomputed from the source before it is sent.",
    included=(
        "Group and summarise rows by the fields you name",
        "Counts, totals, averages and percentages",
        "A consistent report layout with sections and headings",
        "Output as a document or a spreadsheet"),
    excluded=(
        "Conclusions, recommendations or commentary -- the report presents "
        "your figures, it does not interpret them",
        "Charts requiring design judgement",
        "Any figure that cannot be recomputed from the data you sent"),
    deliverables=("The report",
                  "The underlying figures, so every number can be checked"),
    extra_questions=(
        _q("group_by", "What should the report be grouped by?",
           "Month, region, product, customer."),
        _q("measures", "Which figures should it show?",
           "Totals, counts, averages -- and of which column.")),
    minimum_cents=4_000,
    turnaround="Same working day once the order is approved",
    requirements=(
        # One term per section the customer asked for. `report_section`
        # is the only check the report worker treats as an instruction;
        # everything else on this list is a property of the finished
        # document, so an offer without this produces an empty report
        # that then fails every other term at once.
        _term("report_section",
              "each section you asked for is built from your own figures",
              params_from="group_by"),
        _term("report_facts_rendered",
              "the figures you asked for appear",
              params_from="measures"),
        _term("report_opens", "the report opens"),
        _term("report_no_invented_facts",
              "every figure traces to your source"),
        _term("report_missing_disclosed",
              "anything your source did not contain is said to be missing "
              "rather than estimated",
              params_from="measures"),
    ),
)

OFFERS = (CSV_CLEANUP, SPREADSHEET_AUTOMATION, DOCUMENT_PROCESSING,
          DATA_ENTRY, REPORT_BUILDER)

BY_ID = {offer.offer_id: offer for offer in OFFERS}


def offer(offer_id: str) -> ServiceOffer | None:
    return BY_ID.get(offer_id)


# --------------------------------------------------------- order intake

@dataclass
class Order:
    """A customer's request. Not yet a job, and not yet a promise."""

    order_id: str
    offer_id: str
    client_ref: str
    answers: dict
    #: What the customer says they will pay, if the offer is priced openly.
    offered_cents: Cents = 0
    state: OrderState = OrderState.RECEIVED
    why: str = ""
    #: Set once the order has entered the pipeline, so the two can be
    #: reconciled later. An order that reached the pipeline twice would be
    #: two jobs for one payment.
    opportunity_id: str = ""
    missing: tuple[str, ...] = ()

    def customer_status(self) -> str:
        """What to tell the customer. No internal vocabulary."""
        return {
            OrderState.RECEIVED: "Received. Nothing has been charged.",
            OrderState.NEEDS_INFORMATION:
                "We need a little more before we can start: "
                + ", ".join(self.missing),
            OrderState.IN_QUALIFICATION:
                "Being reviewed. You will hear before any work begins.",
            OrderState.DECLINED: f"We are not able to take this on. {self.why}",
            OrderState.IN_PROGRESS: "In progress.",
            OrderState.DELIVERED: "Delivered and checked against your "
                                  "requirements.",
            OrderState.IN_REVISION: "Your revision is being worked on.",
            OrderState.CLOSED: "Complete.",
        }[self.state]


class StorefrontSource(WorkSource):
    """Inbound orders, presented to Discovery as what they are: work.

    This is the join between the two channels. An order becomes an
    ``Opportunity`` here and then follows the identical path to anything
    found on a board -- qualification, the Governor, owner approval,
    execution, verification, delivery, invoice, reconciliation.

    It is not a fixture: the orders are real requests from real people. It
    carries ``structured_terms`` because the customer answered a form with
    named fields rather than writing a paragraph, which is a stronger
    statement of what they want than prose -- though still a client
    assertion, and still qualified like one.
    """

    kind = "storefront"
    is_fixture = False
    structured_offer_terms = True
    work_type = access.CLIENT_SERVICES
    host = ""

    def __init__(self, name: str = "storefront") -> None:
        self.name = name
        self._pending: list[Order] = []

    def enqueue(self, order: Order) -> None:
        self._pending.append(order)

    def request(self):
        """No network call: the orders are already here."""
        return None

    def parse(self, payload: object) -> list:
        out = []
        for order in self._pending:
            service = offer(order.offer_id)
            if service is None:
                continue
            out.append(Opportunity(
                id=f"opp_{order.order_id}",
                source=self.name,
                external_ref=order.order_id,
                title=f"{service.title} for {order.client_ref}",
                quoted_cents=order.offered_cents,
                needs=(service.capability,),
                client_ref=order.client_ref,
                posting=quarantine(
                    json.dumps(order.answers, sort_keys=True),
                    source=self.name, privacy=PrivacyClass.CLIENT_CONFIDENTIAL),
                structured_terms=True,
                deliverables=service.deliverables,
                payment_structure="FIXED",
            ))
        self._pending.clear()
        return out


class Storefront:
    """Takes orders. Decides nothing about them.

    Validates an order against its offer, and hands the ones that are
    complete to Discovery. Whether the work is worth doing, what it should
    cost and whether to accept it are all decided downstream by the
    authorities that already decide those things.
    """

    def __init__(self, capability=None, policy=None, source=None):
        self._capability = capability
        self._policy = policy
        self.source = source if source is not None else StorefrontSource()
        self._orders: dict[str, Order] = {}

    # ---- what may be advertised at all ------------------------------

    def sellable(self, offer_id: str) -> tuple[bool, str]:
        """May this offer be shown to a customer?

        An advert is a promise. The one thing worse than not finding a
        customer is finding one for work that cannot be delivered, so an
        offer whose capability is not cleared for deployment cannot be
        opened -- and the refusal names the capability rather than saying
        "unavailable".
        """
        service = offer(offer_id)
        if service is None:
            return False, f"there is no offer {offer_id!r}"
        if self._capability is None:
            return False, ("no capability registry was supplied, so whether "
                           "this can be delivered is unknown")
        allowed, why = self._capability.may_deploy(service.capability)
        if not allowed:
            return False, (f"{service.capability} is not cleared for "
                           f"delivery: {why}")
        unverifiable = offer_problem(service)
        if unverifiable:
            return False, unverifiable
        floor = self._minimum_fee()
        if floor and service.minimum_cents < floor:
            return False, (
                f"this offer starts at {service.minimum_cents / 100:,.2f}, "
                f"below the owner's {floor / 100:,.2f} minimum")
        return True, f"{service.capability} is cleared and the price clears "\
                     f"the owner's floor"

    def _minimum_fee(self) -> Cents:
        if self._policy is None:
            return 0
        return int(self._policy.get("commercial", "minimum_project_fee_cents",
                                    default=0) or 0)

    def open_offers(self) -> list:
        """Every offer that may currently be shown, with its reason."""
        return [{"offer": o.to_dict(), "sellable": self.sellable(o.offer_id)[0],
                 "why": self.sellable(o.offer_id)[1]} for o in OFFERS]

    # ---- taking an order ---------------------------------------------

    def place(self, *, order_id: str, offer_id: str, client_ref: str,
              answers: dict, offered_cents: Cents = 0) -> Order:
        """Accept a customer's request, or say exactly what is missing."""
        if order_id in self._orders:
            raise FailClosed(
                f"order {order_id!r} already exists; taking it twice would "
                "be two jobs for one payment")
        service = offer(offer_id)
        if service is None:
            raise FailClosed(f"there is no offer {offer_id!r}")

        sellable, why = self.sellable(offer_id)
        if not sellable:
            order = Order(order_id, offer_id, client_ref, dict(answers),
                          offered_cents, OrderState.DECLINED, why)
            self._orders[order_id] = order
            return order

        missing = service.missing_answers(answers)
        if missing:
            order = Order(order_id, offer_id, client_ref, dict(answers),
                          offered_cents, OrderState.NEEDS_INFORMATION,
                          "required answers are missing", missing=missing)
            self._orders[order_id] = order
            return order

        floor = max(service.minimum_cents, self._minimum_fee())
        if offered_cents and offered_cents < floor:
            order = Order(
                order_id, offer_id, client_ref, dict(answers), offered_cents,
                OrderState.DECLINED,
                f"this service starts at {floor / 100:,.2f}")
            self._orders[order_id] = order
            return order

        order = Order(order_id, offer_id, client_ref, dict(answers),
                      offered_cents or floor, OrderState.RECEIVED)
        self._orders[order_id] = order
        return order

    def submit_to_pipeline(self, order_id: str) -> Order:
        """Hand a complete order to Discovery. The only way out of here.

        There is deliberately no method that executes an order. The
        storefront takes requests; the pipeline does the work.
        """
        order = self._orders[order_id]
        # Checked before the state, and the order matters. A resubmitted
        # order fails both conditions, and "you already sent this one" is
        # the useful answer; "it is not in the RECEIVED state" is true and
        # tells the caller nothing about the duplicate they just created.
        # With the checks the other way round the second one was
        # unreachable, which a test caught.
        if order.opportunity_id:
            raise FailClosed(
                f"order {order_id!r} already entered the pipeline as "
                f"{order.opportunity_id}; a second entry would be two jobs "
                "for one payment")
        if order.state is not OrderState.RECEIVED:
            raise FailClosed(
                f"order {order_id!r} is {order.state.value}, not RECEIVED; "
                "only a complete order enters the pipeline")
        self.source.enqueue(order)
        order.opportunity_id = f"opp_{order.order_id}"
        order.state = OrderState.IN_QUALIFICATION
        return order

    def order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def orders(self) -> list:
        return list(self._orders.values())
