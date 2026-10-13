"""Local, cloud, hybrid — and the one line that decides the whole question.

**The five commercial skills call no model.** Everything else here follows
from that, so it is checked first and checked structurally, not asserted in
a comment: a test walks the deterministic modules and fails if any of them
can reach the router at all.

That is not conservatism. A verifier can recompute a sum from the client's
source and get the same answer twice; it cannot recompute a generated
sentence. The moment generated text can reach a deliverable, deterministic
verification stops meaning anything, and T1 — which every job below the
high-value threshold relies on — becomes a formality.
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from solvent.inference import (
    CallCost, InferenceRouter, Purpose, Route, RouteState, RoutingDecision,
)
from solvent.types import PrivacyClass

#: The deterministic delivery path. Nothing in here may reach a model.
DETERMINISTIC_MODULES = (
    "csvwork.py", "sheetwork.py", "docwork.py", "dataentry.py",
    "reportbuilder.py", "checks.py", "sheetverify.py", "csvverify.py",
)


def reachable(route=True, detail="reachable"):
    return lambda _route, _kind, _model: (route, detail)


class Routing(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/ai.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def configure(self, **ai):
        self.s.policy.amend({"ai": ai}, OWNER, "test: route configuration")

    def router(self, probe=None):
        return InferenceRouter(self.s.policy, self.s.audit, probe)


class TheDeliveryPathCallsNoModel(unittest.TestCase):
    """The property the rest of the design rests on."""

    def test_no_deterministic_module_can_reach_the_router(self):
        offenders = []
        for name in DETERMINISTIC_MODULES:
            path = pathlib.Path("solvent") / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in (r"\bfrom \.inference import", r"\bimport inference",
                            r"\bInferenceRouter\b", r"\bfrom \.\.? *roflo",
                            r"\bimport roflo\b"):
                if re.search(pattern, text):
                    offenders.append(f"{name}: {pattern}")
        self.assertEqual(
            offenders, [],
            "a deterministic skill can reach an inference route. Generated "
            "text in a deliverable makes deterministic verification "
            "meaningless:\n" + "\n".join(offenders))

    def test_the_skills_still_say_so_themselves(self):
        """If the docstring promise goes, the guarantee probably went too."""
        sheet = pathlib.Path("solvent/sheetwork.py").read_text(encoding="utf-8")
        self.assertIn("Nothing is generated, inferred", sheet)

    def test_the_router_refuses_a_purpose_outside_the_advisory_set(self):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        s = Solvent(f"{box.name}/x.db")
        self.addCleanup(s.store.close)
        router = InferenceRouter(s.policy)
        with self.assertRaises(FailClosed) as caught:
            router.decide("CLEAN_THE_SPREADSHEET")
        self.assertIn("deterministic skills do not call a model",
                      str(caught.exception))

    def test_every_advisory_purpose_ends_at_a_person(self):
        """Documented intent, pinned so a fifth purpose has to justify itself."""
        self.assertEqual(
            {p.name for p in Purpose},
            {"AMBIGUITY_SUMMARY", "CLIENT_DRAFT", "REQUIREMENT_SUGGESTION",
             "REFUSAL_EXPLANATION"})


class NothingIsReadyUntilItIsConfigured(Routing):

    def test_an_unconfigured_installation_routes_nowhere(self):
        decision = self.router().decide(Purpose.CLIENT_DRAFT)
        self.assertFalse(decision.permitted)
        self.assertIn("not usable", decision.why)

    def test_configured_but_unpermitted_is_not_ready(self):
        self.configure(local_backend="ollama", local_model="q", route="LOCAL")
        status = self.router().local_status()
        self.assertIs(status.state, RouteState.NOT_PERMITTED)

    def test_permitted_but_unprobed_is_unknown_not_ready(self):
        """UNKNOWN is a distinct answer from READY, and from broken."""
        self.configure(local_backend="ollama", local_model="q",
                       permit_local=True)
        status = self.router().local_status()
        self.assertIs(status.state, RouteState.UNKNOWN)
        self.assertFalse(status.usable)

    def test_permitted_and_reachable_is_ready(self):
        self.configure(local_backend="ollama", local_model="q",
                       permit_local=True)
        status = self.router(reachable()).local_status()
        self.assertIs(status.state, RouteState.READY)

    def test_a_configured_route_that_does_not_answer_is_unreachable(self):
        self.configure(local_backend="ollama", local_model="q",
                       permit_local=True)
        status = self.router(reachable(False, "connection refused")).local_status()
        self.assertIs(status.state, RouteState.UNREACHABLE)
        self.assertIn("connection refused", status.detail)

    def test_a_cloud_route_without_a_key_is_unconfigured(self):
        self.configure(cloud_base_url="https://api.test", cloud_model="m",
                       permit_cloud=True, route="CLOUD")
        status = self.router(reachable()).cloud_status()
        self.assertIs(status.state, RouteState.UNCONFIGURED)
        self.assertIn("no cloud API key", status.detail)

    def test_the_key_itself_is_never_stored_in_policy(self):
        """Only whether one exists. The Control Centre reads this table."""
        self.configure(cloud_base_url="https://api.test", cloud_model="m",
                       cloud_key_present=True, permit_cloud=True)
        blob = str(self.s.policy.get("ai", "cloud_key_present"))
        self.assertEqual(blob, "True")
        self.assertIsNone(self.s.policy.get("ai", "cloud_api_key",
                                            default=None))

    def test_an_unrecognised_route_is_refused_not_guessed(self):
        self.configure(route="MAGIC")
        with self.assertRaises(FailClosed) as caught:
            self.router().configured_route
        self.assertIn("not a reason to pick one", str(caught.exception))


class HybridFallsBackLoudly(Routing):

    def both(self, local_ok: bool):
        self.configure(route="HYBRID", local_backend="ollama",
                       local_model="q", permit_local=True,
                       cloud_base_url="https://api.test", cloud_model="m",
                       cloud_key_present=True, permit_cloud=True)

        def probe(route, _kind, _model):
            if route is Route.LOCAL:
                return local_ok, "local" if local_ok else "local is down"
            return True, "cloud reachable"

        return self.router(probe)

    def test_hybrid_prefers_local_when_local_works(self):
        decision = self.both(True).decide(Purpose.CLIENT_DRAFT)
        self.assertIs(decision.chosen, Route.LOCAL)
        self.assertFalse(decision.fell_back)

    def test_hybrid_falls_back_to_cloud_and_says_it_will_be_charged(self):
        decision = self.both(False).decide(Purpose.CLIENT_DRAFT)
        self.assertIs(decision.chosen, Route.CLOUD)
        self.assertTrue(decision.fell_back,
                        "a silent fallback to a paid endpoint is a bill "
                        "nobody authorised")
        self.assertIn("charged", decision.why)

    def test_hybrid_with_nothing_usable_refuses(self):
        self.configure(route="HYBRID")
        decision = self.router().decide(Purpose.CLIENT_DRAFT)
        self.assertFalse(decision.permitted)
        self.assertIn("neither route is usable", decision.why)


class ClientContentDoesNotLeaveQuietly(Routing):
    """Cloud plus the client's own documents is an egress decision."""

    def cloudy(self):
        self.configure(route="CLOUD", cloud_base_url="https://api.test",
                       cloud_model="m", cloud_key_present=True,
                       permit_cloud=True)
        return self.router(reachable())

    def test_a_purpose_carrying_client_content_is_refused_by_default(self):
        decision = self.cloudy().decide(
            Purpose.AMBIGUITY_SUMMARY,
            privacy=PrivacyClass.CLIENT_CONFIDENTIAL)
        self.assertFalse(decision.permitted)
        self.assertIn("has not permitted client content", decision.why)

    def test_an_unstated_privacy_class_is_also_refused(self):
        decision = self.cloudy().decide(Purpose.REQUIREMENT_SUGGESTION)
        self.assertFalse(decision.permitted)
        self.assertIn("no privacy class was stated", decision.why)

    def test_a_purpose_not_carrying_client_content_is_allowed(self):
        decision = self.cloudy().decide(Purpose.REFUSAL_EXPLANATION)
        self.assertIs(decision.chosen, Route.CLOUD)

    def test_the_owner_can_permit_it_explicitly(self):
        self.configure(route="CLOUD", cloud_base_url="https://api.test",
                       cloud_model="m", cloud_key_present=True,
                       permit_cloud=True, permit_cloud_client_content=True)
        decision = self.router(reachable()).decide(
            Purpose.AMBIGUITY_SUMMARY,
            privacy=PrivacyClass.CLIENT_CONFIDENTIAL)
        self.assertIs(decision.chosen, Route.CLOUD)

    def test_hybrid_will_not_use_client_content_as_a_reason_to_go_cloud(self):
        self.configure(route="HYBRID", cloud_base_url="https://api.test",
                       cloud_model="m", cloud_key_present=True,
                       permit_cloud=True)
        decision = self.router(reachable()).decide(
            Purpose.AMBIGUITY_SUMMARY,
            privacy=PrivacyClass.CLIENT_CONFIDENTIAL)
        self.assertFalse(decision.permitted,
                         "a local outage is not consent to send a client's "
                         "documents to a third party")


class WhatACallCost(Routing):

    def test_a_local_call_has_no_per_token_charge(self):
        cost = self.router().cost_of(Route.LOCAL, None)
        self.assertTrue(cost.measured)
        self.assertEqual(cost.cents, 0)
        self.assertIn("already in the compute line", cost.basis)

    def test_a_cloud_call_with_no_reported_usage_is_unknown(self):
        cost = self.router().cost_of(Route.CLOUD, None)
        self.assertFalse(cost.measured)
        self.assertEqual(cost.cents, 0)
        self.assertIn("must not be guessed", cost.basis)

    def test_tokens_without_a_recorded_price_are_unknown(self):
        cost = self.router().cost_of(
            Route.CLOUD, {"prompt_tokens": 900, "completion_tokens": 300})
        self.assertFalse(cost.measured)
        self.assertEqual(cost.total_tokens, 1200)
        self.assertIn("no cloud price is recorded", cost.basis)

    def test_with_a_recorded_price_the_cost_is_measured(self):
        self.configure(cloud_cents_per_1k_input=30,
                       cloud_cents_per_1k_output=150)
        cost = self.router().cost_of(
            Route.CLOUD, {"prompt_tokens": 2000, "completion_tokens": 1000})
        self.assertTrue(cost.measured)
        self.assertEqual(cost.cents, 60 + 150)
        self.assertIn("c/1k", cost.basis)


if __name__ == "__main__":
    unittest.main()
