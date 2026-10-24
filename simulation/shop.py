"""One DeskPilot, set up the way an owner would set one up.

Every call here is one an owner makes: promote the capabilities,
register the work sources, record the contracting party, write the
policy. There is no test fixture, no stub and no substitute
component -- what the simulation measures is a ``Solvent`` built from
``solvent.harness``, ticked by ``solvent.runtime.Runtime``.

The numbers in :data:`POLICY` are the owner's decisions, not
DeskPilot's, and they are the ones a run should be read against: a
margin floor of 30%, a $40 minimum, a $5,000 ceiling on work
delivered but not yet paid for. Change them and the run says
something different, which is the point of their being policy.

**Simulation stays on.** ``egress.simulation_only`` is true and never
turned off, and ``solvent.egress`` installs an audit hook that denies
``socket.connect`` and ``subprocess.Popen`` process-wide and cannot
be removed. No outreach is sent, no payment rail is connected --
``simulation.profile`` deliberately omits the tax reference so
``PAYMENT_RAIL`` stays blocked -- and no real money moves.
"""

from __future__ import annotations

import pathlib

from solvent.discovery import Compliance, ManualSource, Readiness
from solvent.storefront import OFFERS
from solvent.harness import (
    OWNER, Solvent, owner_promotions, promote_capability,
)

from simulation import profile

#: The owner this run acts for: the identity a fresh ``Solvent`` is
#: bootstrapped with, imported rather than chosen. An identity a
#: component picks for itself is not an owner identity --
#: ``PolicyStore.amend`` refuses one that is not in
#: ``governance.owner_identities``, which is how this module learned
#: not to invent one.
OWNER_IDENTITY = OWNER

#: Where a client's written enquiry lands. A ``ManualSource`` because
#: that is what it is: work the owner was told about and entered, with
#: no adapter, no network and no platform terms. The codebase calls it
#: "the *only* acquisition path that is blocked by nothing external".
INBOX = "inbox"

#: The owner's commercial and operational decisions.
POLICY = {
    "discovery": {"approved_sources": ["storefront", INBOX]},
    "qualification": {"minimum_value_cents": 1_000,
                      "max_concurrent_jobs": 5, "default_hours": 2.0},
    "financial": {
        "max_job_spend_cents": 500_000,
        # High enough that the owner is not woken for ordinary work,
        # and low enough that something unusual still reaches them.
        "owner_approval_above_cents": 250_000,
        # The ceiling on work delivered and not yet paid for. The one
        # number that decides how much a shop can lose to a client who
        # never pays, so it is small and deliberate.
        "max_committed_unverified_cents": 500_000,
        "margin_floor": 0.30,
    },
    # The owner's cost model. Every line is a real cost of running
    # this business, and the negotiation floor is computed from them:
    # with none of them recorded, DeskPilot's own costs are near zero,
    # every job looks like a 100% margin, and it quotes a few dollars
    # for work worth a hundred. Measured before these were set --
    # agreed prices of $9.29 and $14.32, both below the owner's own
    # minimum fee, which the storefront then refused.
    "commercial": {
        "minimum_project_fee_cents": 4_000,
        "minimum_margin_fraction": 0.30,
        # A $5 VPS, charged by the CPU hour it actually uses.
        "compute_cents_per_cpu_hour": 1_200,
        # A card processor's cut.
        "payment_fee_fraction": 0.029,
        "payment_fee_flat_cents": 30,
        # The checker's run, the owner's five minutes, the revision
        # some fraction of jobs need, and what it cost to find the
        # client in the first place. Leaving any of them out is how a
        # business prices itself out of existence while every
        # individual job looks profitable.
        "verification_cents_per_job": 2_000,
        "expected_revision_cents_per_job": 4_000,
        "acquisition_cents_per_job": 3_000,
        "supervision_cents_per_job": 2_500,
        "risk_allowance_cents_per_job": 500,
    },
    "contracting": {"turnaround_days": 3},
    # How much work the owner is willing to owe at once. Without
    # this, `Capacity.room_for` refuses everything -- "DeskPilot
    # takes nothing on until the owner says how much it may owe" --
    # which is correct and fails closed, and which is why a shop
    # configured without it quotes nobody. Noted while measuring:
    # the storefront path admitted work anyway, because it is bounded
    # by `qualification.max_concurrent_jobs` and the Operator's
    # per-run limit rather than by this. Two capacity authorities
    # giving different answers to "is there room".
    "capacity": {
        "skills": [offer.capability for offer in OFFERS],
        "cpu_share": 0.5,
        "min_free_disk_bytes": 1,
        # Five skills, four hours of work owed in each.
        "max_owed_seconds": 72_000.0,
    },
    "operations": {"continuous_enabled": True, "max_jobs_per_run": 5,
                   "max_committed_cents": 5_000_000},
    "egress": {
        "simulation_only": True,
        # Delivery goes through the Action Gate, which refuses a
        # destination that is not listed here. The destination is
        # `client.example.com` for every job because
        # `harness` passes that literal -- the Gate never sees the
        # client's own address, so an allowlist can only ever say
        # "all clients" or "no clients". Noted as a finding; for a
        # run in which delivery is simulated anyway it is listed, or
        # nothing is ever delivered and the run measures the gap
        # instead of the business.
        "allowlist": [{"host": "client.example.com",
                       "max_privacy": "CLIENT_CONFIDENTIAL",
                       "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
    },
    "permissions": {"C2_EXTERNAL_COMMUNICATION": "PREAUTHORIZED"},
    "preauthorizations": [{
        "action_class": "C2_EXTERNAL_COMMUNICATION",
        "max_cents": 500_000,
        "why": "the owner's bounded standing grant for unattended delivery",
    }],
}


def open_a_shop(root, *, policy: dict | None = None):
    """Build and configure one DeskPilot. Returns the ``Solvent``.

    ``root`` is a directory this run owns. The database and the work
    directories live under it, and nothing outside it is touched.
    """
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    solvent = Solvent(str(root / "deskpilot.db"))

    # Policy first, and the owner identity with it. Everything below
    # asks `PolicyStore.is_owner`, which reads
    # `governance.owner_identities`; promoting a capability before
    # that list exists is refused, correctly, because an identity
    # nobody registered is not an owner.
    settings = dict(POLICY)
    settings["operations"] = {**POLICY["operations"],
                              "work_root": str(root / "work")}
    for section, values in (policy or {}).items():
        settings[section] = {**settings.get(section, {}), **values}
    solvent.policy.amend(settings, OWNER, "the owner configured the shop")

    for name in owner_promotions():
        promote_capability(solvent, name, owner_identity=OWNER,
                           why="the owner cleared this skill for delivery")

    solvent.discovery.register_source(
        solvent.storefront.source, owner_identity=OWNER,
        readiness=Readiness.PERMITTED_AUTOMATION,
        compliance=Compliance.PERMITTED,
        determination="inbound orders placed against published offers")
    solvent.discovery.register_source(
        ManualSource(INBOX, []), owner_identity=OWNER,
        readiness=Readiness.PERMITTED_AUTOMATION,
        compliance=Compliance.PERMITTED,
        determination="written enquiries the owner entered by hand")

    # Who is asking to be paid. Fictional, and says so in the audit
    # record; without it no invoice can be issued at all, because
    # `may_contract_for("INVOICE")` fails closed on a missing name.
    profile.record(solvent, owner_identity=OWNER)
    return solvent


def what_is_blocked(solvent) -> dict:
    """The controls this run leaves standing, for a report to state.

    Read from the authorities rather than asserted, so the answer is
    what the running system says and not what this module believes.
    """
    return {
        "simulation_only": bool(solvent.policy.get("egress", "simulation_only",
                                                   default=False)),
        "may_invoice": profile.may_invoice(solvent),
        "may_connect_a_payment_rail":
            profile.may_connect_a_payment_rail(solvent),
    }
