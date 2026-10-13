"""Attacking the routing layer.

A router that picks where prompts go, and whether they leave the machine, is
a security boundary. These are the attacks that would matter: getting client
content off the host, getting a paid endpoint called without authorisation,
getting a credential into a log, and getting generated text into a
deliverable that is supposed to be deterministically verifiable.
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from solvent.inference import InferenceRouter, Purpose, Route, RouteState
from solvent.types import PrivacyClass

UP = lambda *_: (True, "reachable")  # noqa: E731


class Attack(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/attack.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def configure(self, **ai):
        self.s.policy.amend({"ai": ai}, OWNER, "attack fixture")

    def router(self, probe=UP):
        return InferenceRouter(self.s.policy, self.s.audit, probe)

    def fully_cloud(self, **extra):
        self.configure(route="CLOUD", cloud_base_url="https://api.test",
                       cloud_model="m", cloud_key_present=True,
                       permit_cloud=True, **extra)
        return self.router()


class GettingClientContentOffTheMachine(Attack):

    def test_a_local_outage_is_not_consent(self):
        """The most plausible route: wait for local to fail."""
        self.configure(route="HYBRID", local_backend="ollama",
                       local_model="q", permit_local=True,
                       cloud_base_url="https://api.test", cloud_model="m",
                       cloud_key_present=True, permit_cloud=True)
        router = self.router(
            lambda route, *_: (route is not Route.LOCAL, "local is down"))
        decision = router.decide(Purpose.AMBIGUITY_SUMMARY,
                                 privacy=PrivacyClass.CLIENT_CONFIDENTIAL)
        self.assertFalse(decision.permitted)

    def test_omitting_the_privacy_class_does_not_bypass_the_check(self):
        """Absence must not read as 'nothing sensitive here'."""
        decision = self.fully_cloud().decide(Purpose.AMBIGUITY_SUMMARY)
        self.assertFalse(decision.permitted)
        self.assertIn("no privacy class was stated", decision.why)

    def test_the_lowest_privacy_class_still_needs_the_permission(self):
        decision = self.fully_cloud().decide(
            Purpose.REQUIREMENT_SUGGESTION, privacy=PrivacyClass.PUBLIC)
        self.assertFalse(decision.permitted,
                         "the permission is about the purpose carrying "
                         "client content, not about one document's label")

    def test_permitting_cloud_does_not_also_permit_client_content(self):
        """Two permissions, and the broader one must not imply the narrower."""
        router = self.fully_cloud()
        self.assertTrue(router.cloud_status().usable)
        decision = router.decide(Purpose.AMBIGUITY_SUMMARY,
                                 privacy=PrivacyClass.CLIENT_CONFIDENTIAL)
        self.assertFalse(decision.permitted)


class SpendingMoneyNobodyAuthorised(Attack):

    def test_cloud_is_not_used_without_its_own_permission(self):
        self.configure(route="CLOUD", cloud_base_url="https://api.test",
                       cloud_model="m", cloud_key_present=True)
        status = self.router().cloud_status()
        self.assertIs(status.state, RouteState.NOT_PERMITTED)

    def test_a_fallback_to_a_paid_route_is_always_recorded_as_one(self):
        self.configure(route="HYBRID", local_backend="ollama",
                       local_model="q", permit_local=True,
                       cloud_base_url="https://api.test", cloud_model="m",
                       cloud_key_present=True, permit_cloud=True)
        decision = self.router(
            lambda route, *_: (route is not Route.LOCAL, "down")
        ).decide(Purpose.REFUSAL_EXPLANATION)
        self.assertIs(decision.chosen, Route.CLOUD)
        self.assertTrue(decision.fell_back)

    def test_an_unpriced_cloud_call_is_not_recorded_as_free(self):
        """The dangerous rounding: unknown becoming zero."""
        cost = self.fully_cloud().cost_of(
            Route.CLOUD, {"prompt_tokens": 50_000, "completion_tokens": 9_000})
        self.assertEqual(cost.cents, 0)
        self.assertFalse(cost.measured,
                         "59,000 tokens at an unrecorded price reported as a "
                         "measured zero would understate every margin")

    def test_a_provider_reporting_absurd_usage_is_still_arithmetic(self):
        self.configure(cloud_cents_per_1k_input=30,
                       cloud_cents_per_1k_output=150)
        cost = self.router().cost_of(
            Route.CLOUD, {"prompt_tokens": 10_000_000,
                          "completion_tokens": 10_000_000})
        self.assertTrue(cost.measured)
        self.assertEqual(cost.cents, 300_000 + 1_500_000)


class CredentialsAndLogs(Attack):

    def test_no_route_report_contains_anything_key_shaped(self):
        self.fully_cloud()
        blob = repr(self.router().report())
        for pattern in (r"sk[-_]live", r"wh" + r"sec_", r"Bearer ",
                        r"[0-9a-f]{40,}"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, blob))

    def test_the_policy_table_holds_no_key(self):
        """It is read by the Control Centre and copied by every backup."""
        self.fully_cloud()
        block = self.s.policy.get("ai")
        self.assertIsInstance(block, dict)
        for key, value in block.items():
            with self.subTest(key=key):
                self.assertNotIn("api_key", key)
                if isinstance(value, str):
                    self.assertFalse(re.search(r"sk[-_]|Bearer ", value))

    def test_the_cloud_backend_never_stores_a_key_in_its_own_report(self):
        from roflo.backends import load_backend
        from roflo.config import BackendConfig

        backend = load_backend(BackendConfig(
            kind="cloud", model="m", base_url="https://api.test",
            api_key="sk" + "-live-not-a-real-key-000000"))
        self.assertTrue(backend.configured)
        self.assertNotIn("not-a-real-key", backend.unconfigured_reason())
        self.assertNotIn("not-a-real-key", repr(backend.payload(
            "hello", _params())))


class GeneratedTextReachingADeliverable(Attack):

    def test_there_is_no_advisory_purpose_that_produces_output(self):
        for purpose in Purpose:
            with self.subTest(purpose=purpose.name):
                self.assertNotIn("DELIVER", purpose.name)
                self.assertNotIn("WRITE", purpose.name)
                self.assertNotIn("GENERATE", purpose.name)

    def test_a_made_up_purpose_is_refused_rather_than_coerced(self):
        for attempt in ("DELIVERABLE", Purpose.CLIENT_DRAFT.value, 7, None):
            with self.subTest(attempt=attempt), self.assertRaises(FailClosed):
                self.router().decide(attempt)

    def test_the_router_is_not_imported_anywhere_in_delivery(self):
        """Checked again here, from the attacker's side rather than the
        designer's: is there *any* path from a worker to a model?"""
        offenders = []
        for path in sorted(pathlib.Path("solvent").glob("*.py")):
            if path.name in ("inference.py", "cli.py", "web.py"):
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"^from \.inference import|^import roflo",
                         text, re.MULTILINE):
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"a model is reachable from {offenders}")


def _params():
    from roflo.types import SamplingParams

    return SamplingParams()


if __name__ == "__main__":
    unittest.main()
