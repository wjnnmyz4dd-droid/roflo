"""Fictional clients: who they are, what they can spend, how they behave.

The first of the three authorities named in :mod:`simulation`. These
decide how clients behave; DeskPilot decides what the business does
about it; :mod:`simulation.audit` grades. Nothing here reads a grade
and nothing here is imported by :mod:`solvent`.

**Everything is fictional and says so.** Every address is under
``.invalid``, reserved by RFC 2606 and incapable of resolving, so
nothing in this module can reach a real person even by accident. No
outreach is sent: the acts below write to DeskPilot's own records
through its own APIs, which is what a client's email *arriving* looks
like from the inside.

**Determinism without coupling.** Each decision draws from a stream
keyed by the master seed, the customer and the day --
``Random(f"{seed}:{customer_id}:{day}:{act}")`` -- so adding a
customer does not perturb anybody else's choices, and re-running a
day gives the same answers. A guard asserts this module never reaches
for the global ``random`` functions, only seeded instances.

**A temperament is fixed, and does not learn.** A client who haggles
hard haggles hard on day 90. This is a deliberate limit, stated
rather than hidden: it means the run measures DeskPilot adapting to
clients and not clients adapting to DeskPilot, and a reader should
not take a steady conversion rate as evidence that the market would
hold still.

**Two doors, because DeskPilot has two.**

* An **order** customer places a storefront order against a published
  offer. That is the inbound path, complete and unattended: the
  business tick carries it from submission to invoice.
* A **relationship** customer appears in the CRM, replies, haggles,
  signs, pays a deposit and sends their file -- the outbound path,
  which ``dealmaking`` and ``contracting`` now join end to end.

The relationship door has a gap on DeskPilot's side and this module
does not paper over it: **nothing in** ``solvent/`` **puts a prospect
in the CRM.** ``CRM.discover`` has no caller in the package, and no
CLI command or web route reaches it. So :meth:`Population.arrive`
writes the prospect itself, under :meth:`_the_outside_world`, which
is named that way so every such act is greppable. That is honest --
in reality a person or a prospecting run does it, and DeskPilot's
prospecting module is marked EXPERIMENTAL and deliberately unwired --
but it is not DeskPilot doing it, and no report should say otherwise.
"""

from __future__ import annotations

import ast
import pathlib
import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from solvent import certgen
from solvent import storefront as shop
from solvent.crm import INDUSTRIES, Basis, Contact, Evidence, Standing
from solvent.engagement import Acceptance
from solvent.errors import FailClosed

#: The five skills DeskPilot sells. Read from the storefront rather
#: than listed here, so a sixth offer is served without this module
#: being edited -- the hand-written mirror is the defect this
#: codebase keeps producing.
CAPABILITIES = tuple(offer.capability for offer in shop.OFFERS)

#: Sectors come from ``crm.INDUSTRIES`` rather than a list here, for
#: the same reason the capabilities do. They carry no behaviour: an
#: industry that changed how a client haggles would be this module
#: asserting something about the world it cannot support.

#: States a fictional client might be in. Varied rather than fixed
#: because ``CRM.discover`` deduplicates on (company, state): two
#: clients with one name in one state are taken to be one company,
#: which is correct of the real world and was producing collisions
#: here until the names were made unique.
STATES = ("TX", "CA", "NY", "FL", "IL", "WA", "GA", "OH")

#: What a client is called. Deliberately dull.
STEMS = ("Northgate", "Brightwell", "Castleford", "Dunmore", "Elmridge",
         "Fairhaven", "Greystone", "Harlow", "Innisfree", "Jerricot",
         "Kingsmere", "Larkfield", "Mossbank", "Netherby", "Oakhurst")
SUFFIXES = ("Group", "Services", "Partners", "& Co", "Holdings", "Works")


# ---------------------------------------------------------- temperament


@dataclass(frozen=True, slots=True)
class Temperament:
    """How one client behaves. Fixed at creation, and never adapts.

    Every field is a probability or a fraction, drawn once per
    customer. They are deliberately independent: a client who pays
    late is not therefore a hard haggler, because assuming that
    correlation would build the conclusion into the population.
    """

    #: Chance of answering an approach on any given working day.
    replies: float
    #: Where their counteroffer lands, as a fraction of the asking
    #: price. 0.7 means "I'll give you seventy per cent of that".
    counters_at: float
    #: Working days between accepting terms and the deposit clearing.
    deposit_takes_days: int
    #: Working days between the invoice and the balance clearing.
    balance_takes_days: int
    #: Chance of asking for changes once the work arrives.
    asks_for_changes: float
    #: Chance, per working day, of simply stopping -- no reply, no
    #: refusal, nothing. The most common way a deal actually dies,
    #: and it applies only while the client is still *uncommitted*:
    #: somebody who has agreed a price and signed does not ghost,
    #: they decline or they pay late. A flat daily rate applied to
    #: everybody compounds -- at 1-8% a day nobody survives ninety
    #: days, and a run would measure the rate rather than the shop.
    goes_quiet: float
    #: Chance of coming back with new work after settling.
    comes_back: float


def a_temperament(rng: random.Random) -> Temperament:
    return Temperament(
        replies=rng.uniform(0.10, 0.55),
        counters_at=rng.uniform(0.55, 0.95),
        deposit_takes_days=rng.randint(0, 4),
        balance_takes_days=rng.randint(1, 21),
        asks_for_changes=rng.uniform(0.05, 0.35),
        goes_quiet=rng.uniform(0.01, 0.06),
        comes_back=rng.uniform(0.05, 0.40),
    )


# ------------------------------------------------------------- customers


@dataclass
class Customer:
    """One fictional client, with an identity that persists all run."""

    customer_id: str
    company: str
    industry: str
    state: str
    email: str
    #: The most they will pay for one job, in cents. A price above it
    #: is refused however well it is argued.
    budget_cents: int
    wants: str
    temperament: Temperament
    #: The day they first appear. Nothing happens before it.
    arrives_on: int
    #: ``"order"`` or ``"relationship"``.
    door: str
    #: Everything that has happened to them, appended by the acts
    #: below. The simulation's own record, kept apart from
    #: DeskPilot's, so the audit can compare the two.
    history: list[dict] = field(default_factory=list)
    #: Set once they stop responding. Terminal for the run.
    quiet_since: int | None = None
    #: How many jobs they have settled. Drives repeat business.
    settled: int = 0

    @property
    def prospect_id(self) -> str:
        return f"client:{self.customer_id}"

    def note(self, day: int, what: str, detail: str = "") -> None:
        self.history.append({"day": day, "what": what, "detail": detail})


def a_customer(rng: random.Random, *, index: int, arrives_on: int,
               door: str) -> Customer:
    stem = rng.choice(STEMS)
    suffix = rng.choice(SUFFIXES)
    slug = f"{stem.lower()}-{index:03d}"
    return Customer(
        customer_id=slug,
        # The index is part of the name, not decoration. Fifteen
        # stems and six suffixes is ninety combinations, and a
        # ninety-day run meets far more clients than that, so without
        # it two of them become one company in the CRM.
        company=f"{stem} {suffix} {index:03d}",
        industry=rng.choice(INDUSTRIES),
        state=rng.choice(STATES),
        # `.invalid` is reserved by RFC 2606 and cannot resolve.
        email=f"ops@{slug}.invalid",
        # Wide on purpose. A population that can all afford the asking
        # price measures nothing about pricing.
        budget_cents=rng.randrange(5_000, 90_000, 500),
        wants=rng.choice(CAPABILITIES),
        temperament=a_temperament(rng),
        arrives_on=arrives_on,
        door=door,
    )


# ------------------------------------------------------------ population


@dataclass
class Population:
    """Everybody who will ever appear, and what they do each day.

    ``arrivals_per_day`` is how many new clients turn up on a working
    day; non-working days bring nobody, because the people who place
    orders are also the people who take Saturdays off.
    """

    seed: str
    length_days: int
    arrivals_per_day: float = 1.0
    #: What fraction come through the storefront rather than the CRM.
    order_fraction: float = 0.5
    customers: list[Customer] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.seed:
            raise ValueError(
                "an unseeded population is not reproducible, and a figure "
                "that cannot be reproduced cannot be audited")
        if self.arrivals_per_day < 0:
            raise ValueError("a negative arrival rate is not a slower market")
        if not 0.0 <= self.order_fraction <= 1.0:
            raise ValueError("order_fraction is a fraction of the arrivals")

    def stream(self, *parts) -> random.Random:
        """One deterministic stream per (customer, day, act).

        Keyed rather than sequential so that adding a customer, or
        skipping an act, does not shift anybody else's draws. A single
        shared generator would make every run depend on the order the
        acts happened to be called in.
        """
        return random.Random(":".join([self.seed, *(str(p) for p in parts)]))

    def build(self, working_days: Sequence[int]) -> list[Customer]:
        """Decide who arrives when. Call once, before the run starts.

        ``working_days`` is the day indices clients are willing to
        turn up on -- the calendar's, not DeskPilot's, which works
        every day.
        """
        self.customers = []
        index = 0
        for day in working_days:
            rng = self.stream("arrivals", day)
            whole = int(self.arrivals_per_day)
            extra = 1 if rng.random() < (self.arrivals_per_day - whole) else 0
            for _ in range(whole + extra):
                index += 1
                door = ("order" if self.stream("door", index).random()
                        < self.order_fraction else "relationship")
                self.customers.append(
                    a_customer(self.stream("who", index), index=index,
                               arrives_on=day, door=door))
        return self.customers

    def by_id(self) -> dict:
        return {c.customer_id: c for c in self.customers}

    def active(self, day: int) -> list[Customer]:
        """Arrived, and not yet gone quiet."""
        return [c for c in self.customers
                if c.arrives_on <= day and c.quiet_since is None]

    # ---- the materials a client sends --------------------------------

    def materials_for(self, customer: Customer, *, workroot: pathlib.Path,
                      nth: int = 1) -> dict:
        """The file and the form answers this client hands over.

        Built from :mod:`solvent.certgen`, the same generators the
        capability certification uses, so the work is representative
        rather than a toy. Written once per job and left on disk,
        because the business cycle opens it later and a file produced
        on demand would hide a missing one.
        """
        offer = shop.offer(customer.wants)
        if offer is None:                       # pragma: no cover - guarded
            raise FailClosed(f"no offer sells {customer.wants!r}")
        rng = self.stream("materials", customer.customer_id, nth)
        case = _a_case(customer.wants, rng)
        workroot.mkdir(parents=True, exist_ok=True)
        path = workroot / f"{customer.customer_id}-{nth}-{case.source_name or 'source.dat'}"
        path.write_bytes(case.source_bytes
                         or (case.source_text or "").encode("utf-8"))

        answers = {key: "as the offer describes" for key in offer.intake_keys()}
        answers["source_file"] = str(path)
        for term in offer.requirements:
            if not term.params_from:
                continue
            if term.check == "report_section":
                # A list answer, which the offer expands into one
                # requirement per section. Taken from the generator's
                # own parameters rather than invented here.
                facts = case.params.get("report_facts_rendered", {})
                total = case.params.get("report_total_correct", {})
                answers[term.params_from] = [
                    {"kind": "facts", "heading": "Overview",
                     "fields": facts.get("fields", [])},
                    {"kind": "table", "heading": "Lines"},
                    {"kind": "total", "heading": "Totals",
                     "column": total.get("column", "amount")},
                ]
                continue
            answers[term.params_from] = dict(case.params.get(term.check, {}))
        return answers


def _a_case(capability: str, rng: random.Random):
    """One generated case from the production generators."""
    import inspect

    generator = certgen.GENERATORS[capability]
    if "index" in inspect.signature(generator).parameters:
        return generator(rng, 0)
    return generator(rng)


# ---------------------------------------------------------------- guards


FORBIDDEN_CALLS = frozenset({
    "now", "utcnow", "today", "monotonic", "perf_counter", "time_ns",
    "urandom", "uuid4",
})
FORBIDDEN_IMPORTS = frozenset({"time", "uuid", "secrets"})


def nondeterminism_in(path: pathlib.Path) -> set[str]:
    """What the code at ``path`` reaches for that no seed controls.

    ``random`` is *not* forbidden here, unlike in
    :mod:`simulation.calendar`: a seeded ``Random`` instance is the
    whole mechanism. What is forbidden is the module-level functions,
    which share one global generator nobody seeded -- caught
    separately below, because ``random.random()`` and
    ``Random(seed).random()`` are the same attribute name and only the
    receiver tells them apart.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    return (FORBIDDEN_CALLS & used) | (FORBIDDEN_IMPORTS & imported)


def unseeded_random_in(path: pathlib.Path) -> set[str]:
    """Calls on the ``random`` *module* rather than on a ``Random``.

    ``random.choice(...)`` draws from a global generator seeded from
    the system clock. ``self.stream(...).choice(...)`` draws from one
    this module seeded. They are the same attribute, so the receiver
    is what is inspected: only a bare ``random.X(...)`` is a finding.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "random"
                # `random.Random(seed)` is the constructor, not a draw.
                and func.attr != "Random"):
            found.add(f"random.{func.attr}")
    return found


def nondeterminism_in_this_module() -> set[str]:
    here = pathlib.Path(__file__)
    return nondeterminism_in(here) | unseeded_random_in(here)
