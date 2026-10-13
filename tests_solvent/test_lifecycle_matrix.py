"""The 210, executed against the real authorities.

Each stage is probed with the authority that actually governs it, not with
a stand-in. A matrix that asks a mock whether something is permitted
learns nothing about whether it is.
"""

from __future__ import annotations

import collections
import pathlib
import tempfile
import unittest

from tests_solvent.fixtures import set_commercial_terms
from solvent import sourceaccess as access
from solvent import storefront as sf
from solvent.content import quarantine
from solvent.discovery import Compliance, FixtureSource, Opportunity, Readiness
from solvent.finder import Finder
from solvent.harness import OWNER, Solvent, owner_promotions, promote_capability
from solvent.types import PrivacyClass
from tests_solvent.lifecyclematrix import STAGES, scenarios


class Board(FixtureSource):
    is_fixture = False
    kind = "marketplace"
    work_type = access.CLIENT_SERVICES


class Lifecycle(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.box = tempfile.TemporaryDirectory()
        cls.dir = pathlib.Path(cls.box.name)
        cls.s = Solvent(str(cls.dir / "life.db"))
        set_commercial_terms(cls.s)
        for name in owner_promotions():
            promote_capability(cls.s, name, owner_identity=OWNER,
                               why="test: cleared for the lifecycle matrix")
        cls.shop = sf.Storefront(cls.s.capability, cls.s.policy)
        for source in (Board("outbound_board", []), cls.shop.source):
            cls.s.discovery.register_source(
                source, owner_identity=OWNER,
                readiness=Readiness.PERMITTED_AUTOMATION,
                compliance=Compliance.PERMITTED, determination="matrix")
        cls.s.policy.amend(
            {"commercial": {"minimum_project_fee_cents": 4_000}},
            OWNER, "test: the owner's floor")
        cls.finder = Finder(cls.s.discovery, cls.s.policy, cls.s.capability,
                            cls.s.orchestrator, cls.s.store)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.s.store.close()
        except Exception:
            pass
        cls.box.cleanup()

    def opportunity(self, scenario):
        source = ("storefront" if scenario.channel == "inbound"
                  else "outbound_board")
        return Opportunity(
            id=f"opp_{scenario.name}", source=source,
            external_ref=scenario.name, title=f"{scenario.skill} work",
            quoted_cents=scenario.cents, needs=(scenario.skill,),
            client_ref="client:matrix",
            posting=quarantine("Fixed price for the project.", source=source,
                               privacy=PrivacyClass.CLIENT_CONFIDENTIAL),
            structured_terms=(scenario.channel == "inbound"),
            payment_structure="FIXED")

    # ---- one prober per stage, each asking the real authority ----------

    def probe_qualify(self, scenario):
        assessment = self.finder.assess(self.opportunity(scenario),
                                        extension="xlsx")
        if assessment.verdict.value == "PURSUE":
            return "PROCEED", assessment.why
        if "external authority" in assessment.why:
            return "REFUSE_TIER", assessment.why
        return "REFUSE_OTHER", assessment.why

    def probe_claim(self, scenario):
        allowed, why = self.s.discovery.may(
            self.opportunity(scenario).source, access.ACCEPT_CONTRACT)
        if allowed:
            return "PROCEED", why
        return "REFUSE_PERMISSION", why

    def probe_execute(self, scenario):
        allowed, why = self.s.capability.may_deploy(scenario.skill)
        return ("PROCEED" if allowed else "REFUSE_PERMISSION"), why

    def probe_verify(self, scenario):
        from solvent.orchestrator import REQUIRED_TIER
        from solvent.types import VerificationTier

        tier = REQUIRED_TIER[self.s.policy.consequence_for(scenario.cents)]
        if tier.rank <= VerificationTier.T1_DETERMINISTIC.rank:
            return "PROCEED", f"{tier.value} from the certified verifier"
        return "REFUSE_TIER", f"{tier.value} is needed"

    def probe_submit(self, scenario):
        allowed, why = self.s.discovery.may(
            self.opportunity(scenario).source, access.SUBMIT_APPLICATION)
        return ("PROCEED" if allowed else "REFUSE_PERMISSION"), why

    def probe_invoice(self, scenario):
        shortfall = self.s.policy.contracting_shortfall("INVOICE")
        if shortfall:
            return "REFUSE_IDENTITY", f"missing: {', '.join(shortfall)}"
        return "PROCEED", "a contracting identity is recorded"

    def probe_reconcile(self, scenario):
        real = self.s.ledger.real_revenue_cents()
        if real:
            return "PROCEED", f"{real} cents of verified real revenue"
        return "REFUSE_SIMULATED", "no real revenue has ever been collected"

    def probe(self, scenario):
        return getattr(self, f"probe_{scenario.stage}")(scenario)


class TheTwoHundredAndTen(Lifecycle):

    def test_the_matrix(self):
        failures = []
        counts = collections.Counter()
        for scenario in scenarios():
            with self.subTest(scenario=scenario.name):
                got, why = self.probe(scenario)
                counts[f"{scenario.stage}:{got}"] += 1
                if got != scenario.expect:
                    failures.append(
                        f"{scenario.name}: expected {scenario.expect} "
                        f"({scenario.because}), got {got} ({why})")

        self.assertEqual(sum(counts.values()), 210,
                         "the matrix must run all two hundred and ten")
        self.assertEqual(
            failures, [],
            f"{len(failures)} of 210 disagree with the rules:\n"
            + "\n".join(failures[:12]))

    def test_every_stage_is_covered(self):
        covered = {s.stage for s in scenarios()}
        self.assertEqual(covered, {name for name, _ in STAGES})

    def test_every_stage_has_a_real_prober(self):
        """A matrix that asks a mock learns nothing about the system."""
        for stage, _ in STAGES:
            with self.subTest(stage=stage):
                self.assertTrue(hasattr(self, f"probe_{stage}"))


class WhatTheMatrixEstablishes(Lifecycle):

    def by_stage(self, stage):
        return [s for s in scenarios() if s.stage == stage]

    def test_nothing_may_be_claimed_on_any_source(self):
        """ACCEPT_CONTRACT is granted by nobody, and no terms permit it."""
        for scenario in self.by_stage("claim")[:6]:
            with self.subTest(scenario=scenario.name):
                got, _ = self.probe(scenario)
                self.assertNotEqual(got, "PROCEED")

    def test_nothing_may_be_submitted_on_any_source(self):
        for scenario in self.by_stage("submit")[:6]:
            with self.subTest(scenario=scenario.name):
                got, _ = self.probe(scenario)
                self.assertEqual(got, "REFUSE_PERMISSION")

    def test_no_invoice_is_valid_without_an_identity(self):
        got, why = self.probe(self.by_stage("invoice")[0])
        self.assertEqual(got, "REFUSE_IDENTITY")
        self.assertIn("legal_name", why)

    def test_no_simulated_payment_counts_as_revenue(self):
        got, why = self.probe(self.by_stage("reconcile")[0])
        self.assertEqual(got, "REFUSE_SIMULATED")
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_work_can_still_be_done_and_verified(self):
        """The two stages that do pass, so the matrix is not all refusals."""
        for stage in ("execute", "verify"):
            with self.subTest(stage=stage):
                scenario = [s for s in self.by_stage(stage)
                            if s.value_label == "500"][0]
                got, why = self.probe(scenario)
                self.assertEqual(got, "PROCEED", why)


if __name__ == "__main__":
    unittest.main()
