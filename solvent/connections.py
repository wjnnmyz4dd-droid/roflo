"""Whether the owner's own accounts are connected, and saying so honestly.

Two outward channels need an account the owner holds: a business
mailbox and a payment processor. This module reports the state of
both for the Business Connections cards, and the whole design is
about one rule:

    **DeskPilot cannot assert that an account is connected.**

It can report what the policy records, and it can tell the owner what
to do next. It cannot decide that a mailbox works, because that is a
claim about a credential in somebody else's secret store and a
message that actually left. The payment side already works this way
-- :meth:`PolicyStore.advance_payment_rail` refuses to self-assert
CONFIGURED or LIVE_VERIFIED -- and the email side is modelled on it
rather than inventing a looser one.

Reuse, not a second system. Stripe's state is read from the existing
payment-rail ladder; the Connect button writes the existing
``advance_payment_rail`` intent, which is already CONSEQUENTIAL and
already needs the owner's approval. Nothing here holds a credential,
and connecting an account does not enable anything: live sending and
live charging remain behind simulation mode and the Action Gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(Enum):
    """What the card shows. Three states, and no optimistic fourth."""

    NOT_CONNECTED = "NOT CONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


#: The email ladder, deliberately the same shape as the payment one.
EMAIL_SELECTED = "SELECTED"
EMAIL_ENGINEERING_READY = "ENGINEERING_READY"
EMAIL_CONFIGURED = "CONFIGURED"
EMAIL_LIVE_VERIFIED = "LIVE_VERIFIED"
EMAIL_STATES = (EMAIL_SELECTED, EMAIL_ENGINEERING_READY, EMAIL_CONFIGURED,
                EMAIL_LIVE_VERIFIED)

#: What each rung actually claims. Written out because the difference
#: between "our side is built" and "a message was delivered" is the
#: difference between a working channel and an embarrassing one.
EMAIL_MEANING = {
    EMAIL_SELECTED: "a provider has been chosen and nothing else is true yet",
    EMAIL_ENGINEERING_READY: "DeskPilot's side is built and tested against a "
                             "recorded provider response; no account is "
                             "attached",
    EMAIL_CONFIGURED: "the owner has attached an account and authorised it "
                      "with the provider",
    EMAIL_LIVE_VERIFIED: "a real message has been observed leaving and "
                         "arriving",
}


@dataclass(frozen=True, slots=True)
class Card:
    """One connection, as the dashboard shows it."""

    key: str
    title: str
    state: State
    detail: str
    #: The intent verb the button writes. Empty when there is nothing
    #: to do from here.
    action_verb: str = ""
    action_label: str = ""
    #: True when the next step is something only the owner can do
    #: outside DeskPilot, which the card should say rather than
    #: offering a button that cannot work.
    owner_must_act_elsewhere: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "state": self.state.value,
                "detail": self.detail, "action_verb": self.action_verb,
                "action_label": self.action_label,
                "owner_must_act_elsewhere": self.owner_must_act_elsewhere}


def stripe_card(policy) -> Card:
    """Read the existing payment rail. Never a second payment system."""
    try:
        rail = policy.get("payment", "approved_rail", default="") or ""
        state = policy.get("payment", "rail_state", default="") or ""
    except Exception as problem:                       # a read must not break a page
        return Card("stripe", "Stripe Payments", State.ERROR,
                    f"the payment policy could not be read: {problem}")

    if not rail:
        return Card("stripe", "Stripe Payments", State.NOT_CONNECTED,
                    "No payment rail has been chosen yet.",
                    "advance_payment_rail", "CONNECT STRIPE")
    if rail.lower() != "stripe":
        return Card("stripe", "Stripe Payments", State.NOT_CONNECTED,
                    f"The approved rail is {rail!r}, not Stripe.",
                    "advance_payment_rail", "CONNECT STRIPE")
    if state == "LIVE_VERIFIED":
        return Card("stripe", "Stripe Payments", State.CONNECTED,
                    "A real payment has been observed arriving.")
    if state == "CONFIGURED":
        return Card("stripe", "Stripe Payments", State.CONNECTED,
                    "An account is attached. No payment has been observed "
                    "arriving through it yet.")
    return Card("stripe", "Stripe Payments", State.NOT_CONNECTED,
                f"Stripe is selected and the rail is at {state or 'SELECTED'}. "
                "DeskPilot cannot mark it configured on its own: attaching "
                "the account happens in Stripe, and the owner records it "
                "here afterwards.",
                "advance_payment_rail", "CONNECT STRIPE",
                owner_must_act_elsewhere=True)


def email_card(policy) -> Card:
    """The same ladder, for the mailbox."""
    try:
        provider = policy.get("email", "provider", default="") or ""
        state = policy.get("email", "state", default="") or ""
    except Exception as problem:
        return Card("email", "Business Email", State.ERROR,
                    f"the email policy could not be read: {problem}")

    if not provider:
        return Card("email", "Business Email", State.NOT_CONNECTED,
                    "No mailbox has been connected. DeskPilot prepares "
                    "messages but cannot send any.",
                    "connect_business_email", "CONNECT BUSINESS EMAIL")
    if state == EMAIL_LIVE_VERIFIED:
        return Card("email", "Business Email", State.CONNECTED,
                    f"{provider}: a message has been observed leaving and "
                    "arriving.")
    if state == EMAIL_CONFIGURED:
        return Card("email", "Business Email", State.CONNECTED,
                    f"{provider}: an account is attached. Nothing has been "
                    "sent through it yet.")
    return Card("email", "Business Email", State.NOT_CONNECTED,
                f"{provider} is selected and the connection is at "
                f"{state or EMAIL_SELECTED}. "
                f"{EMAIL_MEANING.get(state or EMAIL_SELECTED, '')}. "
                "Authorising the mailbox happens with the provider; "
                "DeskPilot never asks for a password.",
                "connect_business_email", "CONNECT BUSINESS EMAIL",
                owner_must_act_elsewhere=True)


def cards(policy) -> list[Card]:
    return [email_card(policy), stripe_card(policy)]


def live_actions_enabled(policy) -> bool:
    """Whether connecting has actually turned anything on. Normally no.

    Kept next to the cards so the dashboard can say the thing owners
    most need to hear: an attached account is not a running business.
    Simulation is still the gate, and it is a separate decision.
    """
    try:
        return not bool(policy.get("egress", "simulation_only", default=True))
    except Exception:
        return False


def summary(policy) -> dict:
    return {"cards": [c.as_dict() for c in cards(policy)],
            "live_external_actions_enabled": live_actions_enabled(policy),
            "note": ("Connecting an account does not enable live sending or "
                     "live charging. Those stay behind simulation mode and "
                     "the Action Gate.")}
