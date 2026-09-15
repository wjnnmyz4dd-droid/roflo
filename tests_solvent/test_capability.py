"""Conformance before price, and unknown conformance fails closed."""

import unittest

from solvent.capability import Capability, CapabilityRegistry
from solvent.content import RequirementSet, confirm, extract_requirements, quarantine
from solvent.errors import FailClosed
from solvent.types import CapabilityVerdict, Conformance
from tests_solvent.fixtures import OWNER, Rig


class Registration(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.registry = CapabilityRegistry(self.rig.store, self.rig.audit)

    def test_solvent_cannot_register_a_capability_for_itself(self):
        """Solvent may propose growth; it may never authorise it."""
        for who in ("execution", "analyst", "learning", "orchestrator"):
            with self.subTest(who=who), self.assertRaises(FailClosed):
                self.registry.register(
                    Capability(name="x", covers=frozenset({"x"})), owner_identity=who)

    def test_owner_may_register(self):
        self.registry.register(
            Capability(name="spreadsheet", covers=frozenset({"spreadsheet"}),
                       proven=True), owner_identity=OWNER)
        self.assertEqual(len(self.registry.capabilities()), 1)


class Assessment(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.registry = CapabilityRegistry(self.rig.store, self.rig.audit)
        self.registry.register(
            Capability(name="spreadsheet", covers=frozenset({"spreadsheet"}),
                       proven=True), owner_identity=OWNER)

    def _requirements(self, confirmed=True):
        content = quarantine("build a sheet", source="email")
        drafts = extract_requirements(content, ["monthly spreadsheet"])
        if confirmed:
            return RequirementSet(items=[confirm(d, by=OWNER) for d in drafts])
        return RequirementSet(items=list(drafts))

    def test_unconfirmed_requirements_leave_conformance_unknown(self):
        """The conformance-capture defence: injected text cannot establish fit."""
        assessment = self.registry.assess(
            job_id="J1", requirements=self._requirements(confirmed=False),
            assessor="extractor", needs=["spreadsheet"])
        self.assertIs(assessment.conformance, Conformance.UNKNOWN)
        self.assertFalse(assessment.may_commit)

    def test_confirmed_requirements_with_a_proven_capability_conform(self):
        assessment = self.registry.assess(
            job_id="J1", requirements=self._requirements(), assessor=OWNER,
            needs=["spreadsheet"])
        self.assertIs(assessment.conformance, Conformance.CONFORMS)
        self.assertIs(assessment.verdict, CapabilityVerdict.CAN_EXECUTE)
        self.assertTrue(assessment.may_commit)

    def test_an_uncovered_need_fails_conformance(self):
        assessment = self.registry.assess(
            job_id="J1", requirements=self._requirements(), assessor=OWNER,
            needs=["spreadsheet", "video_editing"])
        self.assertIs(assessment.conformance, Conformance.FAILS)
        self.assertIn("video_editing", assessment.gaps)

    def test_no_requirements_means_nothing_to_conform_to(self):
        assessment = self.registry.assess(
            job_id="J1", requirements=RequirementSet(), assessor=OWNER,
            needs=["spreadsheet"])
        self.assertIs(assessment.verdict, CapabilityVerdict.CANNOT_EXECUTE)
        self.assertFalse(assessment.may_commit)

    def test_an_unproven_capability_needs_owner_approval(self):
        """Absence of evidence is not capability."""
        registry = CapabilityRegistry(self.rig.store, self.rig.audit)
        registry.register(Capability(name="design", covers=frozenset({"design"}),
                                     proven=False), owner_identity=OWNER)
        assessment = registry.assess(job_id="J2", requirements=self._requirements(),
                                     assessor=OWNER, needs=["design"])
        self.assertIs(assessment.verdict, CapabilityVerdict.REQUIRES_OWNER_APPROVAL)
        self.assertFalse(assessment.may_commit)


class ConformanceBeforePrice(unittest.TestCase):
    """The $8,900 trap, made structurally impossible."""

    def test_a_cheaper_non_conforming_option_is_removed_not_ranked(self):
        rig = Rig()
        registry = CapabilityRegistry(rig.store, rig.audit)
        options = [
            {"name": "A", "price_cents": 1_200_000, "provides": ["x", "y"],
             "verified": True},
            {"name": "B", "price_cents": 890_000, "provides": ["x"], "verified": True},
        ]
        conforming, rejected = registry.conforming_options(options, {"x", "y"})
        self.assertEqual([o["name"] for o in conforming], ["A"])
        self.assertEqual([o["name"] for o in rejected], ["B"])
        self.assertNotIn("B", [o["name"] for o in conforming],
                         "the cheaper option must never reach price comparison")

    def test_an_unverified_claim_is_not_conformance(self):
        rig = Rig()
        registry = CapabilityRegistry(rig.store, rig.audit)
        conforming, rejected = registry.conforming_options(
            [{"name": "C", "price_cents": 1, "provides": ["x"], "verified": False}],
            {"x"})
        self.assertEqual(conforming, [])
        self.assertEqual(rejected[0]["conformance"], Conformance.UNKNOWN.value)


if __name__ == "__main__":
    unittest.main()
