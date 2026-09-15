"""Policy Store: owner-only writes, fail-closed reads, kill-switch semantics."""

import unittest

from solvent.audit import AuditLog
from solvent.errors import FailClosed
from solvent.policy import PolicyStore
from solvent.store import Store
from solvent.types import ActionClass, ConsequenceTier, OperatingMode, PermissionLevel

OWNER = "owner:test"


class OwnerOnly(unittest.TestCase):
    def setUp(self):
        store = Store()
        self.policy = PolicyStore(store, AuditLog(store), owner_identity=OWNER)

    def test_non_owner_identity_cannot_amend(self):
        """Learning, the Governor and every worker are all 'not the owner'."""
        for who in ("governor", "learning", "analyst", "orchestrator", "", "alice"):
            with self.subTest(who=who), self.assertRaises(FailClosed):
                self.policy.amend({"financial": {"margin_floor": 0.0}}, who, "lower")

    def test_amendment_requires_a_reason(self):
        with self.assertRaises(FailClosed):
            self.policy.amend({"operating_mode": "NORMAL"}, OWNER, "")

    def test_owner_amendment_versions_rather_than_overwrites(self):
        before = self.policy.version
        self.policy.amend({"financial": {"margin_floor": 0.4}}, OWNER, "raise floor")
        self.assertEqual(self.policy.version, before + 1)
        self.assertEqual(self.policy.get("financial", "margin_floor"), 0.4)
        # History is retained: the old version is still readable.
        rows = self.policy._db.query("SELECT version FROM policy_versions ORDER BY version")
        self.assertGreaterEqual(len(rows), 2)

    def test_amendment_merges_rather_than_replaces_siblings(self):
        self.policy.amend({"financial": {"margin_floor": 0.4}}, OWNER, "raise floor")
        self.assertIsNotNone(self.policy.get("financial", "max_job_spend_cents"))


class Permissions(unittest.TestCase):
    def setUp(self):
        store = Store()
        self.policy = PolicyStore(store, AuditLog(store), owner_identity=OWNER)

    def test_authority_change_is_prohibited_not_merely_gated(self):
        """Solvent may never change its own authority, at any value or urgency."""
        self.assertIs(self.policy.permission_for(ActionClass.C5_AUTHORITY_CHANGE),
                      PermissionLevel.PROHIBITED)

    def test_unknown_action_class_fails_closed_to_owner_approval(self):
        self.policy.amend({"permissions": {}}, OWNER, "wipe permissions")
        doc = self.policy.doc()
        doc["permissions"] = {}
        self.policy._write(doc, OWNER, "force empty")
        self.assertIs(self.policy.permission_for(ActionClass.C3_FINANCIAL_COMMITMENT),
                      PermissionLevel.OWNER_APPROVAL_REQUIRED)

    def test_preauthorization_respects_its_bound(self):
        self.policy.amend({"preauthorizations": [
            {"action_class": ActionClass.C3_FINANCIAL_COMMITMENT.value,
             "max_cents": 30_000}]}, OWNER, "bounded standing grant")
        self.assertIsNotNone(self.policy.preauthorization_for(
            ActionClass.C3_FINANCIAL_COMMITMENT, 29_999))
        self.assertIsNone(self.policy.preauthorization_for(
            ActionClass.C3_FINANCIAL_COMMITMENT, 30_001))


class KillSwitch(unittest.TestCase):
    def setUp(self):
        store = Store()
        self.policy = PolicyStore(store, AuditLog(store), owner_identity=OWNER)

    def test_mode_semantics(self):
        cases = {
            OperatingMode.NORMAL: (False, False, False),
            OperatingMode.PAUSE_NEW_WORK: (True, False, False),
            OperatingMode.PAUSE_SPEND: (True, True, False),
            OperatingMode.PAUSE_EXTERNAL: (True, True, True),
            OperatingMode.SAFE_MODE: (True, True, True),
            OperatingMode.HALT: (True, True, True),
        }
        for mode, (new_work, spend, external) in cases.items():
            with self.subTest(mode=mode):
                self.assertEqual(mode.blocks_new_work, new_work)
                self.assertEqual(mode.blocks_spend, spend)
                self.assertEqual(mode.blocks_external, external)

    def test_halt_records_that_it_abandons_obligations(self):
        """A kill switch that silently creates breach exposure is not a safety feature."""
        audit_events_before = len(self.policy._audit.events())
        self.policy.set_operating_mode(OperatingMode.HALT, OWNER, "emergency")
        events = self.policy._audit.events()[audit_events_before:]
        self.assertTrue(any(e["event"] == "policy.halt" for e in events))
        halt = next(e for e in events if e["event"] == "policy.halt")
        self.assertIn("abandons", halt["why"])


class ConsequenceScaling(unittest.TestCase):
    def setUp(self):
        store = Store()
        self.policy = PolicyStore(store, AuditLog(store), owner_identity=OWNER)

    def test_consequence_tier_scales_with_value(self):
        self.assertIs(self.policy.consequence_for(1_000), ConsequenceTier.C_LOW)
        self.assertIs(self.policy.consequence_for(30_000), ConsequenceTier.C_MED)
        self.assertIs(self.policy.consequence_for(150_000), ConsequenceTier.C_HIGH)
        self.assertIs(self.policy.consequence_for(900_000), ConsequenceTier.C_CRITICAL)

    def test_verification_budget_is_a_fraction_of_value(self):
        """A $50 job must not be allowed to spend $40 proving itself."""
        cheap = self.policy.verification_budget(5_000)
        self.assertLessEqual(cheap, 5_000 * 0.05)

    def test_empty_egress_allowlist_permits_nothing(self):
        self.assertFalse(self.policy.egress_allowed("api.example.com"))
        self.assertFalse(self.policy.egress_allowed(""))


if __name__ == "__main__":
    unittest.main()
