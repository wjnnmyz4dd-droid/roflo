"""The same job, three ways: local, cloud, hybrid.

The question the owner needs answered is "which AI route should DeskPilot
use, and what does it save?" The honest way to answer it is to run the same
paid work under each configuration and measure, which is what this does.

**What is measured here is real.** Identical source data, identical agreed
requirements, identical price, run three times under three route
configurations, with wall time and operating cost taken from the same code
that bills a client.

**What is not measured, and why.** Live inference latency. This machine has
no local model server running and no cloud credential, and the sweep is not
permitted to download models or buy credits. Numbers for "how fast is the
7B locally versus the hosted model" would therefore be invented, so they are
absent rather than estimated. What that costs the comparison is small, for
the reason the benchmark itself demonstrates.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
import unittest

from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.harness import (
    OWNER, Solvent, owner_promotions, promote_capability, run_business_cycle,
)
from solvent.inference import InferenceRouter, Purpose, Route
from solvent.types import Criticality, Requirement, RequirementSource

ROWS = 2_000
PRICE = 40_000


def req(rid, check, params=None):
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params=params or {},
        criticality=Criticality.MANDATORY)


AGREED = [
    req("R1", "require_columns", {"columns": ["Name", "Email", "Amount"]}),
    req("R2", "trim_whitespace", {"columns": ["Name"]}),
    req("R3", "drop_exact_duplicates", {}),
]

#: The three configurations, each complete and permitted, so the only thing
#: differing between runs is the route itself.
ROUTES = {
    "LOCAL": {"route": "LOCAL", "local_backend": "ollama",
              "local_model": "qwen2.5:14b-instruct", "permit_local": True},
    "CLOUD": {"route": "CLOUD", "cloud_base_url": "https://api.example.test",
              "cloud_model": "hosted-model", "cloud_key_present": True,
              "permit_cloud": True, "cloud_cents_per_1k_input": 30,
              "cloud_cents_per_1k_output": 150},
    "HYBRID": {"route": "HYBRID", "local_backend": "ollama",
               "local_model": "qwen2.5:14b-instruct", "permit_local": True,
               "cloud_base_url": "https://api.example.test",
               "cloud_model": "hosted-model", "cloud_key_present": True,
               "permit_cloud": True, "cloud_cents_per_1k_input": 30,
               "cloud_cents_per_1k_output": 150},
}


class SameJobThreeWays(unittest.TestCase):

    maxDiff = None

    def build(self, route_config):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        directory = pathlib.Path(box.name)
        s = Solvent(str(directory / "bench.db"))
        self.addCleanup(lambda: self._close(s))
        for name in owner_promotions():
            promote_capability(s, name, owner_identity=OWNER,
                               why="benchmark: cleared")
        s.discovery.register_source(
            FixtureSource("board", [{
                "ref": "bench", "title": "Clean a customer export",
                "quoted_cents": PRICE, "needs": ["csv-cleanup"],
                "project_state": "CT", "client_ref": "client:bench",
                "body": "fixed price for the project", "deliverables": []}]),
            owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="benchmark")
        s.policy.amend({
            "discovery": {"approved_sources": ["board"]},
            "financial": {"max_job_spend_cents": 500_000,
                          "owner_approval_above_cents": 10_000_000},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
            # The owner's recorded operating costs, identical across runs.
            "commercial": {"compute_cents_per_cpu_hour": 50,
                           "platform_fee_fraction": 0.10,
                           "payment_fee_fraction": 0.029,
                           "payment_fee_flat_cents": 30},
            "ai": route_config,
        }, OWNER, "benchmark: one route, everything else identical")

        source = directory / "export.csv"
        rows = ["Name,Email,Amount"]
        for i in range(ROWS):
            rows.append(f"  Client {i % 700} ,c{i % 700}@x.test,{i % 93}.50")
        source.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return s, source, box.name

    @staticmethod
    def _close(s):
        try:
            s.store.close()
        except Exception:
            pass

    def run_one(self, name):
        s, source, workdir = self.build(ROUTES[name])
        started = time.monotonic()
        cycle = run_business_cycle(
            s, source_name="board", source_file=str(source), workdir=workdir,
            capability="csv-cleanup", agreed_requirements=AGREED,
            owner_accepts_quote=True, simulate_payment=True)
        elapsed = time.monotonic() - started
        self.assertTrue(cycle.completed,
                        f"{name}: {cycle.stopped_at}: {cycle.why_stopped}")
        return {
            "route": name,
            "wall_seconds": round(elapsed, 4),
            "execution_seconds": round(cycle.execution_wall_seconds, 4),
            "invoiced_cents": cycle.invoiced_cents,
            "operating_cost_cents": cycle.operating_cost_cents,
            "profit_cents": cycle.actual_profit_cents,
            "api_cost_line": [b for b in cycle.operating_cost_basis
                              if b.startswith("model API")],
        }

    def test_three_identical_runs(self):
        results = {name: self.run_one(name) for name in ROUTES}

        print("\n  route   wall      execution  invoiced  op cost  profit")
        for name, r in results.items():
            print(f"  {name:7s} {r['wall_seconds']:<9.3f} "
                  f"{r['execution_seconds']:<10.3f} "
                  f"{r['invoiced_cents']:<9d} {r['operating_cost_cents']:<8d} "
                  f"{r['profit_cents']}")

        costs = {r["operating_cost_cents"] for r in results.values()}
        profits = {r["profit_cents"] for r in results.values()}
        self.assertEqual(
            len(costs), 1,
            f"the route changed what delivery cost: {costs}. It should not: "
            "the five skills call no model.")
        self.assertEqual(len(profits), 1, f"the route changed profit: {profits}")

    def test_no_route_spends_a_single_token(self):
        """The finding the whole comparison rests on."""
        for name in ROUTES:
            with self.subTest(route=name):
                result = self.run_one(name)
                self.assertEqual(
                    result["api_cost_line"],
                    ["model API 0 token(s) = $0.00"],
                    f"{name} consumed model tokens delivering a job that is "
                    "supposed to be deterministic")

    def test_the_cloud_route_is_never_reached_by_delivery(self):
        """Configured, permitted, priced — and still never called."""
        s, source, workdir = self.build(ROUTES["CLOUD"])
        router = InferenceRouter(s.policy,
                                 probe=lambda *_: (True, "reachable"))
        self.assertTrue(router.cloud_status().usable,
                        "the cloud route must be genuinely available, or "
                        "this proves nothing")
        cycle = run_business_cycle(
            s, source_name="board", source_file=str(source), workdir=workdir,
            capability="csv-cleanup", agreed_requirements=AGREED,
            owner_accepts_quote=True, simulate_payment=True)
        self.assertTrue(cycle.completed, cycle.why_stopped)
        self.assertEqual(cycle.operating_cost_cents,
                         self.run_one("LOCAL")["operating_cost_cents"])


class WhatRoutingActuallyChanges(unittest.TestCase):
    """If not delivery cost, then what? The advisory surfaces."""

    def router(self, config, probe=None):
        box = tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        s = Solvent(f"{box.name}/r.db")
        self.addCleanup(s.store.close)
        s.policy.amend({"ai": config}, OWNER, "benchmark")
        return InferenceRouter(s.policy, probe=probe)

    def test_each_route_answers_an_advisory_call_differently(self):
        up = lambda *_: (True, "reachable")  # noqa: E731
        outcomes = {}
        for name, config in ROUTES.items():
            decision = self.router(config, up).decide(Purpose.CLIENT_DRAFT)
            outcomes[name] = decision.chosen
        self.assertEqual(outcomes["LOCAL"], Route.LOCAL)
        self.assertEqual(outcomes["CLOUD"], Route.CLOUD)
        self.assertEqual(outcomes["HYBRID"], Route.LOCAL,
                         "hybrid should prefer the route that costs nothing")

    def test_only_the_cloud_route_can_cost_money_per_call(self):
        up = lambda *_: (True, "reachable")  # noqa: E731
        usage = {"prompt_tokens": 4000, "completion_tokens": 1000}
        local = self.router(ROUTES["LOCAL"], up).cost_of(Route.LOCAL, usage)
        cloud = self.router(ROUTES["CLOUD"], up).cost_of(Route.CLOUD, usage)
        self.assertEqual(local.cents, 0)
        self.assertGreater(cloud.cents, 0)
        print(f"\n  one advisory call of {usage['prompt_tokens']} in / "
              f"{usage['completion_tokens']} out: "
              f"local ${local.cents / 100:.4f}, cloud ${cloud.cents / 100:.4f}")


class WhatThisBenchmarkDoesNotMeasure(unittest.TestCase):
    """Stated in code so it cannot quietly drop out of a report."""

    def test_no_live_inference_was_performed(self):
        import shutil

        self.assertIsNone(
            shutil.which("ollama"),
            "a local model server is present after all; this benchmark's "
            "stated limitation is now false and live latency should be "
            "measured instead of declared unmeasurable")

    def test_no_cloud_credential_is_configured(self):
        import os

        for name in ("ROFLO_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            with self.subTest(variable=name):
                self.assertFalse(
                    os.environ.get(name),
                    f"{name} is set; a live cloud benchmark is now possible "
                    "and the stated limitation no longer holds")


if __name__ == "__main__":
    unittest.main()
