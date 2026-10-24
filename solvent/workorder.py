"""Turning a customer's order into the arguments the cycle takes.

Separate from :mod:`solvent.operation` on purpose. That module is the
bounded loop and a test pins its imports to ``errors`` and ``types``
alone, so that the thing deciding *whether to go round again* cannot
quietly grow opinions about the work. This module is allowed to know
about the storefront because resolving an order is exactly what it is
for.

It is not an authority and holds no judgement about whether work is
worth taking. Qualification triages, the Governor prices, the Action
Gate authorises the delivery and the owner approves above the
threshold. All of that is downstream of everything here and none of it
is reimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import FailClosed


class NoOwnerRegistered(FailClosed):
    """There is no owner to act for, so nothing may act."""


def owner_identity_of(policy) -> str:
    """The registered owner this process acts for.

    Read from the policy's own list rather than from a constant, because
    an identity a component chooses for itself is not an owner identity:
    ``PolicyStore.is_owner`` checks membership of this list, so anything
    not in it would be refused at the first authority that asked.

    Raises rather than returning a default. A default owner identity is
    a forged one, and the failure it causes -- work authorised in the
    name of somebody who never registered -- is worse than not starting.
    """
    registered = policy.get("governance", "owner_identities", default=[]) or []
    for identity in registered:
        if isinstance(identity, str) and identity.startswith("owner:"):
            return identity
    raise NoOwnerRegistered(
        "no owner identity is registered in governance.owner_identities, so "
        "there is nobody for this run to act for")


@dataclass(frozen=True, slots=True)
class WorkOrder:
    """One customer order, resolved into what the business cycle needs.

    Carries no judgement. Every field is either copied from the order the
    customer placed or from the published offer they placed it against.
    """

    source_name: str
    capability: str
    source_file: str
    workdir: str
    agreed_requirements: tuple
    owner_identity: str
    external_ref: str
    #: What the customer has already paid toward this work. Copied
    #: from the order, carried no further than the cycle, and
    #: deducted there -- this module judges nothing about it.
    prepaid_cents: int = 0

    def as_cycle_kwargs(self) -> dict:
        """Exactly the keyword arguments ``run_business_cycle`` declares.

        Spelled out rather than splatted from ``__dict__`` so that a
        rename on either side is a failure here, at one place, instead of
        a ``TypeError`` inside a scheduled task nobody is watching.
        """
        return {
            "source_name": self.source_name,
            "source_file": self.source_file,
            "workdir": self.workdir,
            "capability": self.capability,
            "owner_identity": self.owner_identity,
            "agreed_requirements": list(self.agreed_requirements),
            "prepaid_cents": self.prepaid_cents,
            "external_ref": self.external_ref,
        }


def order_problem(service, order, *, source_file: str) -> str:
    """Why this order cannot be executed unattended, or ``""``.

    Separate from the resolution below so that the reasons are a list a
    person can read, and so each one can be tested on its own.
    """
    import os

    from .storefront import offer_problem

    if service is None:
        return f"order {order.order_id!r} names an offer that no longer exists"
    unverifiable = offer_problem(service)
    if unverifiable:
        return unverifiable
    missing = service.missing_answers(order.answers)
    if missing:
        return (f"order {order.order_id!r} is missing {', '.join(missing)}")
    if not source_file:
        return f"order {order.order_id!r} names no source file"
    if not os.path.isfile(source_file):
        # Checked before the job starts rather than when the worker opens
        # it: a job admitted against a file that is not there holds a
        # capacity slot and fails at execution, which costs an attempt and
        # tells the customer nothing useful.
        return (f"the file order {order.order_id!r} names is not there: "
                f"{source_file}")
    for term in service.requirements:
        if not term.params_from:
            continue
        answer = (order.answers or {}).get(term.params_from)
        if isinstance(answer, list):
            # A customer may ask for several of the same thing -- three
            # report sections, say. The list is accepted and expands into
            # one requirement each; an empty one, or one carrying
            # anything but mappings, is not an answer.
            answer = (answer and all(isinstance(x, dict) and x for x in answer)
                      and {"_list": answer}) or None
        if not isinstance(answer, dict) or not answer:
            # Refused rather than guessed. The alternative is inferring a
            # column mapping from a sentence, which is interpretation; the
            # five skills call no model, and a mapping DeskPilot invented
            # would then be verified against DeskPilot's own invention.
            return (f"order {order.order_id!r} answers {term.params_from!r} "
                    f"with something that is not a structured answer, and "
                    f"{term.check} needs one to know what to do")
    return ""


#: Characters a directory name may keep. Everything else is replaced,
#: including the ones that are merely awkward, because the set that is
#: safe everywhere is much smaller than the set that is safe here.
_KEEP = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def workdir_name(order_id: str) -> str:
    """A directory name for an order, safe on every filesystem.

    The order id was used raw, and on Windows that is a real failure
    rather than a tidiness point: an engagement-backed order is
    ``eng-client:northstar``, the colon is NTFS's alternate-data-stream
    separator, and ``os.makedirs`` raises ``NotADirectoryError``
    [WinError 267]. Thirteen tests failed on Windows CI and passed on
    Linux for exactly this reason.

    It is also a boundary. An order id can carry a client's own
    reference, so ``..``, a separator or a drive letter in one would be
    a path chosen by whoever placed the order. Rewriting the name here
    means the directory stays under ``workroot`` whatever arrives.

    The digest is what keeps two different order ids from colliding
    after both have had their awkward characters replaced, and makes
    the name stable: the same order resolves to the same directory on
    every call, which is what lets a resumed job find its own work.
    """
    import hashlib

    cleaned = "".join(c if c in _KEEP else "-" for c in str(order_id))
    # Leading dots and dashes read as hidden files and as option flags;
    # a trailing dot or space is silently stripped by Windows, so two
    # names that differ only there would become one directory.
    cleaned = cleaned.strip("-. ")[:60] or "order"
    digest = hashlib.sha256(str(order_id).encode("utf-8")).hexdigest()[:12]
    return f"{cleaned}-{digest}"


def next_work_order(s, *, workroot: str, owner_identity: str = "") -> "WorkOrder | None":
    """The next customer order to run, or ``None`` when there is none.

    ``None`` is a real answer and the loop above depends on it: an empty
    shop stops the run with NOTHING_TO_DO rather than inventing work to
    look productive.

    Exactly one order enters the pipeline per call. The storefront's
    source hands Discovery everything queued in one ``parse``, and the
    cycle takes the first, so submitting a batch would discover several
    opportunities and execute one -- leaving the rest discovered, not
    run, and not obviously anywhere. Submitting one at a time makes the
    loop's own job-per-iteration bound the thing that paces admission,
    which is where the owner's limits already live.

    Nothing here decides whether the work is worth taking. Qualification
    triages it, the Governor prices it, the Action Gate authorises the
    delivery and the owner approves above the threshold -- all of that is
    downstream of this function and none of it is reimplemented in it.
    """
    import os

    from .storefront import OrderState, offer

    shop = getattr(s, "storefront", None)
    if shop is None:
        return None
    owner = owner_identity or owner_identity_of(s.policy)

    for order in shop.orders():
        if order.state is not OrderState.RECEIVED:
            continue
        service = offer(order.offer_id)
        source_file = str((order.answers or {}).get("source_file", "")).strip()
        problem = order_problem(service, order, source_file=source_file)
        if problem:
            # Recorded and skipped, never silently dropped: an order that
            # cannot run is a thing the customer is waiting on.
            s.audit.record(
                event="operation.order_not_executable", authority="operations",
                initiator="operator", why=problem, decision="SKIPPED",
                result=order.order_id)
            continue

        workdir = os.path.join(workroot, workdir_name(order.order_id))
        os.makedirs(workdir, exist_ok=True)
        requirements = _requirements_of(service, order.answers)
        # Submitted last, so a resolution that raised cannot leave the
        # order in the pipeline with nothing about to run it.
        shop.submit_to_pipeline(order.order_id)
        return WorkOrder(
            source_name=shop.source.name, capability=service.capability,
            source_file=source_file, workdir=workdir,
            agreed_requirements=tuple(requirements), owner_identity=owner,
            prepaid_cents=int(getattr(order, "prepaid_cents", 0) or 0),
            external_ref=order.order_id)
    return None


def _requirements_of(service, answers: dict | None = None) -> list:
    """The offer's published promises, as the checklist the cycle verifies.

    ``RequirementSource.CLIENT_CONFIRMED_STRUCTURED`` because that is what
    they are: the customer chose a published offer with a fixed scope and
    answered a form, rather than writing prose somebody had to interpret.
    The check names come from the offer, which ``offer_problem`` has
    already refused if any of them is not one the capability registers.
    """
    from .types import Criticality, Requirement, RequirementSource

    given = answers or {}
    out = []
    for index, term in enumerate(service.requirements, 1):
        supplied = given.get(term.params_from) if term.params_from else None
        # One requirement per mapping. A list answer becomes several,
        # numbered, because "three sections" is three things to do and a
        # single requirement carrying a list of them would be one thing
        # the worker has to unpack and the checker has to re-unpack.
        variants = supplied if isinstance(supplied, list) else [supplied or {}]
        for order_in_term, extra in enumerate(variants, 1):
            params = dict(term.params)
            params.update(extra or {})
            suffix = "" if len(variants) == 1 else f".{order_in_term}"
            out.append(Requirement(
                id=f"{service.offer_id}-{index}{suffix}", text=term.acceptance,
                source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
                acceptance=term.acceptance, check=term.check,
                params=params, criticality=Criticality.MANDATORY))
    return out
