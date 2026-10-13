"""Which AI route DeskPilot uses, and what it costs — for advisory work only.

The first thing to say is the thing that decides everything else:

**The five commercial skills call no model at all.** Cleaning a spreadsheet,
filling a template, reconciling rows — all of it is deterministic Python.
``sheetwork`` says so in its own docstring ("Nothing is generated, inferred"),
and ``docwork`` says it too. That is not an oversight waiting to be modernised;
it is the reason T1 verification can exist. A verifier can recompute a sum
from the client's source and get the same answer twice. It cannot recompute a
generated sentence.

So the choice between local and cloud inference does **not** change what it
costs to deliver the five skills. That cost is zero tokens, measured, and the
``model API 0 token(s) = $0.00`` line in every operating-cost breakdown is
accurate rather than unimplemented.

Where a model legitimately helps is the work that currently stops and waits
for a person: summarising an ambiguity, drafting a reply, proposing an
interpretation of a prose requirement. Every one of those ends at a human
decision, which is exactly why a non-deterministic tool is safe there and
nowhere else.

This module routes those calls. It owns no model, holds no key, and decides
nothing about a job. It answers one question -- *given what is configured and
reachable, where should an advisory call go, and what will it cost?* -- and
refuses when the answer is "nowhere".

Three routes:

``LOCAL``
    A model on this machine. No marginal cost per call, bounded by the
    hardware, and the client's data never leaves the host.
``CLOUD``
    A hosted endpoint. Costs real money per token and sends the prompt to a
    third party, which is a privacy decision the owner makes once.
``HYBRID``
    Local first, cloud when local cannot serve. The fallback is explicit and
    recorded, never silent: a job that quietly went to a paid endpoint is a
    bill nobody authorised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import FailClosed
from .types import Cents, PrivacyClass


class Route(Enum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"
    HYBRID = "HYBRID"


class RouteState(Enum):
    """What a route can actually do right now. No optimistic defaults."""

    READY = "READY"
    #: Named in policy but missing an address, a key or a model.
    UNCONFIGURED = "UNCONFIGURED"
    #: Configured and did not answer.
    UNREACHABLE = "UNREACHABLE"
    #: Configured and reachable, but the owner has not cleared it for use.
    NOT_PERMITTED = "NOT_PERMITTED"
    #: Never probed. Distinct from READY, and from broken.
    UNKNOWN = "UNKNOWN"


class Purpose(Enum):
    """Where an advisory model may be used. Deliberately a closed set.

    Every one of these ends at a person deciding. Adding a purpose that does
    not is how generated text reaches a client's deliverable, and the
    deterministic verification the whole business rests on stops meaning
    anything.
    """

    #: Summarising a detected ambiguity for the owner to resolve.
    AMBIGUITY_SUMMARY = "AMBIGUITY_SUMMARY"
    #: Drafting a reply for the owner to read, edit and send.
    CLIENT_DRAFT = "CLIENT_DRAFT"
    #: Proposing an interpretation of a prose requirement, for confirmation.
    REQUIREMENT_SUGGESTION = "REQUIREMENT_SUGGESTION"
    #: Explaining a refusal in plain language. Changes no decision.
    REFUSAL_EXPLANATION = "REFUSAL_EXPLANATION"


#: Purposes whose input may contain the client's own documents. A cloud route
#: sends those to a third party, so it needs the egress decision the owner
#: already makes for everything else rather than a new one invented here.
TOUCHES_CLIENT_CONTENT = (Purpose.AMBIGUITY_SUMMARY,
                          Purpose.REQUIREMENT_SUGGESTION)


@dataclass(frozen=True, slots=True)
class RouteStatus:
    route: Route
    state: RouteState
    detail: str
    backend_kind: str = ""
    model: str = ""

    @property
    def usable(self) -> bool:
        return self.state is RouteState.READY


@dataclass(frozen=True, slots=True)
class CallCost:
    """What one advisory call cost. Measured, or explicitly unknown."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cents: Cents = 0
    measured: bool = False
    basis: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class RoutingDecision:
    purpose: Purpose
    chosen: Route | None
    why: str
    considered: tuple[RouteStatus, ...] = ()
    #: True when the configured route could not serve and another did.
    fell_back: bool = False

    @property
    def permitted(self) -> bool:
        return self.chosen is not None


class InferenceRouter:
    """Chooses a route for an advisory call. Owns nothing, decides no job.

    Reads policy for what is configured and what the owner has permitted,
    and probes for what is reachable. It does not construct backends and
    does not hold keys; the caller does that with what this returns.
    """

    def __init__(self, policy, audit=None, probe=None):
        self._policy = policy
        self._audit = audit
        #: Injected so reachability can be tested without a server. The
        #: default probes nothing and says so, rather than assuming yes.
        self._probe = probe

    # ---- what is configured -------------------------------------------

    def _ai(self, key, default=None):
        return self._policy.get("ai", key, default=default)

    @property
    def configured_route(self) -> Route:
        raw = str(self._ai("route", "LOCAL") or "LOCAL").upper()
        try:
            return Route(raw)
        except ValueError:
            raise FailClosed(
                f"ai.route is {raw!r}, which is not one of "
                f"{[r.value for r in Route]}; an unrecognised route is not a "
                "reason to pick one")

    def local_status(self) -> RouteStatus:
        kind = str(self._ai("local_backend", "") or "")
        model = str(self._ai("local_model", "") or "")
        if not kind:
            return RouteStatus(Route.LOCAL, RouteState.UNCONFIGURED,
                               "no local backend is configured "
                               "(ai.local_backend)")
        if not model:
            return RouteStatus(Route.LOCAL, RouteState.UNCONFIGURED,
                               f"{kind} is configured but names no model "
                               "(ai.local_model)", backend_kind=kind)
        if not self._permitted("local"):
            return RouteStatus(Route.LOCAL, RouteState.NOT_PERMITTED,
                               "the owner has not permitted local inference "
                               "(ai.permit_local)", backend_kind=kind,
                               model=model)
        return self._probed(Route.LOCAL, kind, model)

    def cloud_status(self) -> RouteStatus:
        base_url = str(self._ai("cloud_base_url", "") or "")
        model = str(self._ai("cloud_model", "") or "")
        has_key = bool(self._ai("cloud_key_present", False))
        if not base_url:
            return RouteStatus(Route.CLOUD, RouteState.UNCONFIGURED,
                               "no cloud endpoint is configured "
                               "(ai.cloud_base_url)")
        if not model:
            return RouteStatus(Route.CLOUD, RouteState.UNCONFIGURED,
                               "a cloud endpoint is configured but names no "
                               "model (ai.cloud_model)", backend_kind="cloud")
        if not has_key:
            # The key itself is never stored in policy -- only whether one
            # has been provisioned. A credential in the policy table is a
            # credential in every backup of it.
            return RouteStatus(Route.CLOUD, RouteState.UNCONFIGURED,
                               "no cloud API key has been provisioned",
                               backend_kind="cloud", model=model)
        if not self._permitted("cloud"):
            return RouteStatus(
                Route.CLOUD, RouteState.NOT_PERMITTED,
                "the owner has not permitted cloud inference, which sends "
                "prompts to a third party and costs money per call "
                "(ai.permit_cloud)", backend_kind="cloud", model=model)
        return self._probed(Route.CLOUD, "cloud", model)

    def _permitted(self, which: str) -> bool:
        return bool(self._ai(f"permit_{which}", False))

    def _probed(self, route: Route, kind: str, model: str) -> RouteStatus:
        if self._probe is None:
            return RouteStatus(route, RouteState.UNKNOWN,
                               "configured and permitted; reachability has "
                               "not been probed", backend_kind=kind,
                               model=model)
        reachable, detail = self._probe(route, kind, model)
        state = RouteState.READY if reachable else RouteState.UNREACHABLE
        return RouteStatus(route, state, detail, backend_kind=kind,
                           model=model)

    # ---- choosing -------------------------------------------------------

    def decide(self, purpose: Purpose, *,
               privacy: PrivacyClass | None = None) -> RoutingDecision:
        """Where should this advisory call go, if anywhere?"""
        if not isinstance(purpose, Purpose):
            raise FailClosed(
                f"{purpose!r} is not an advisory purpose. The deterministic "
                "skills do not call a model, and adding a purpose for them "
                "would end deterministic verification")

        local = self.local_status()
        cloud = self.cloud_status()
        considered = (local, cloud)
        route = self.configured_route

        if route is Route.LOCAL:
            if local.usable:
                return RoutingDecision(purpose, Route.LOCAL,
                                       f"local is ready ({local.model})",
                                       considered)
            return RoutingDecision(
                purpose, None,
                f"local is the configured route and is not usable: "
                f"{local.detail}", considered)

        if route is Route.CLOUD:
            blocked = self._privacy_refusal(purpose, privacy)
            if blocked:
                return RoutingDecision(purpose, None, blocked, considered)
            if cloud.usable:
                return RoutingDecision(purpose, Route.CLOUD,
                                       f"cloud is ready ({cloud.model})",
                                       considered)
            return RoutingDecision(
                purpose, None,
                f"cloud is the configured route and is not usable: "
                f"{cloud.detail}", considered)

        # HYBRID: local first, because it costs nothing per call and keeps
        # the client's content on this machine.
        if local.usable:
            return RoutingDecision(purpose, Route.LOCAL,
                                   f"hybrid: local served it ({local.model})",
                                   considered)
        blocked = self._privacy_refusal(purpose, privacy)
        if blocked:
            return RoutingDecision(
                purpose, None,
                f"hybrid: local is unusable ({local.detail}) and {blocked}",
                considered)
        if cloud.usable:
            return RoutingDecision(
                purpose, Route.CLOUD,
                f"hybrid: local is unusable ({local.detail}), so this falls "
                f"back to cloud ({cloud.model}) and will be charged",
                considered, fell_back=True)
        return RoutingDecision(
            purpose, None,
            f"hybrid: neither route is usable. local: {local.detail}; "
            f"cloud: {cloud.detail}", considered)

    def _privacy_refusal(self, purpose, privacy) -> str:
        """Cloud plus the client's own content is an egress decision.

        Not a new authority: the owner already decides what may leave this
        machine and at what privacy class. This refuses rather than quietly
        inventing a second, weaker rule for prompts.
        """
        if purpose not in TOUCHES_CLIENT_CONTENT:
            return ""
        if privacy is None:
            return ("this purpose can carry the client's own content and no "
                    "privacy class was stated, so it is not sent off this "
                    "machine")
        if not bool(self._ai("permit_cloud_client_content", False)):
            return (f"this purpose carries {privacy.value} content and the "
                    "owner has not permitted client content to reach a "
                    "third-party endpoint (ai.permit_cloud_client_content)")
        return ""

    # ---- what it cost ---------------------------------------------------

    def cost_of(self, route: Route, usage: dict | None) -> CallCost:
        """Turn reported tokens into money. Unknown stays unknown.

        A local call costs nothing per token -- the electricity and the
        hardware are already in the compute line of the operating cost, and
        counting them twice would overstate what a job cost.
        """
        if route is Route.LOCAL:
            return CallCost(
                measured=True,
                basis="local inference: no per-token charge; the machine's "
                      "time is already in the compute line")
        if not usage:
            return CallCost(
                measured=False,
                basis="the endpoint reported no usage, so the cost of this "
                      "call is not known and must not be guessed")

        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        in_rate = float(self._ai("cloud_cents_per_1k_input", 0) or 0)
        out_rate = float(self._ai("cloud_cents_per_1k_output", 0) or 0)
        if in_rate <= 0 and out_rate <= 0:
            return CallCost(
                prompt_tokens=prompt, completion_tokens=completion,
                measured=False,
                basis=f"{prompt + completion} token(s) used, but no cloud "
                      "price is recorded (ai.cloud_cents_per_1k_input/output), "
                      "so what they cost is not known")
        cents = round(prompt / 1000.0 * in_rate + completion / 1000.0 * out_rate)
        return CallCost(
            prompt_tokens=prompt, completion_tokens=completion,
            cents=int(cents), measured=True,
            basis=f"{prompt} in at {in_rate:g}c/1k + {completion} out at "
                  f"{out_rate:g}c/1k = {cents / 100:,.4f}")

    # ---- reporting ------------------------------------------------------

    def report(self) -> dict:
        """What each route can do, for the owner to choose between."""
        local, cloud = self.local_status(), self.cloud_status()
        return {
            "configured_route": self.configured_route.value,
            "routes": [
                {"route": s.route.value, "state": s.state.value,
                 "detail": s.detail, "backend": s.backend_kind,
                 "model": s.model}
                for s in (local, cloud)],
            "delivery_path_uses_a_model": False,
            "note": ("the five commercial skills are deterministic and call "
                     "no model; routing affects advisory surfaces only"),
        }
