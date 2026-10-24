"""The fictional business DeskPilot trades as, for simulation only.

Production refuses to invent this. ``PolicyStore.contracting_party``
says so in its own docstring -- *"There is no code path here that
produces a name nobody provided"* -- and ``may_contract_for("INVOICE")``
fails closed until a legal name, an address and an email are recorded.
That is correct: a system that guesses its owner's legal identity onto
a document a client reads is a system that forges one.

The consequence, measured before this module existed, was that no
invoice could be issued in any run, so no payment could settle, so no
capacity was ever returned and the shop seized after a dozen jobs. The
business could not be simulated because it could not bill anybody.

So the owner is supplied here rather than invented there. Every value
is fictional and says so. The address uses ``.invalid``, reserved by
RFC 2606 precisely so that it can never resolve.

This records an owner decision through the **existing** policy
authority. It adds no second authority, stores no credential, and
touches no payment rail: ``tax_reference`` is deliberately omitted, so
``PAYMENT_RAIL`` stays blocked and no simulated run can drift into
thinking it may move real money.
"""

from __future__ import annotations

#: Fictional. Not a real company; the name is not registered anywhere.
LEGAL_NAME = "Northstar Business Solutions LLC"
#: Fictional. `.invalid` is reserved by RFC 2606 and cannot resolve.
EMAIL = "billing@northstar.invalid"
#: Fictional. "Test City, CT 00000" is not a deliverable address.
ADDRESS = "100 Simulation Plaza, Test City, CT 00000"
#: Fictional owner, named so traces are readable.
OWNER_NAME = "Alex Morgan"
#: The structure the owner contracts under.
STRUCTURE = "LLC"

#: Why this is recorded, carried into the audit log verbatim so that
#: anybody reading the trail later can see it was a simulation.
REASON = ("SIMULATION ONLY: fictional contracting identity so invoices "
          "can be issued in a synthetic business run. Not a real "
          "company, not a real address, no payment rail provisioned.")


def record(solvent, *, owner_identity: str) -> int:
    """Record the fictional party through the real policy authority.

    Returns the new policy version. Deliberately calls
    ``record_contracting_structure`` rather than writing the policy
    document directly, so the owner-identity check, the field
    validation and the audit record all happen exactly as they would
    for a real owner.

    ``tax_reference`` is **not** supplied. INVOICE needs legal name,
    address and email; PAYMENT_RAIL additionally needs a tax reference,
    and leaving it out keeps that purpose blocked. A simulation that
    could satisfy PAYMENT_RAIL would be one step from believing it may
    connect a real rail.
    """
    return solvent.policy.record_contracting_structure(
        owner_identity=owner_identity, structure=STRUCTURE, reason=REASON,
        legal_name=LEGAL_NAME, address=ADDRESS, email=EMAIL)


def may_invoice(solvent) -> tuple[bool, str]:
    """Ask the production authority whether an invoice may be issued."""
    return solvent.policy.may_contract_for("INVOICE")


def may_connect_a_payment_rail(solvent) -> tuple[bool, str]:
    """Must stay false. Exposed so a test can assert it stays false."""
    return solvent.policy.may_contract_for("PAYMENT_RAIL")
