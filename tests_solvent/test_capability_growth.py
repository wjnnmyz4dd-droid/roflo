"""A detected gap becomes a question, and only the owner answers it.

The black-box certification found that `REQUIRES_NEW_CAPABILITY` existed as a
verdict and registration was owner-only, but nothing connected them: every gap
produced the same refusal under every owner decision, so obedience to
approved / denied / limited was untestable rather than proven. These are the
tests that were impossible to write before.
"""

from __future__ import annotations

import unittest

from solvent.capability import Capability, CapabilityRegistry as Reg
from solvent.content import RequirementSet
from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from solvent.types import (
    CapabilityVerdict, Requirement, RequirementSource as RS,
)


def owner_confirmed():
    return RequirementSet(items=[Requirement(
        id="r1", text="Deliver it as a formatted workbook.",
        source=RS.OWNER_CONFIRMED, confirmed_by=OWNER)])


def evidence(name, **overrides):
    base = dict(name=name, covers=frozenset({name}), proven=True,
                version=f"{name}/1.0", verifiable_by=("a", "b", "c"),
                proven_levels=("L1", "L2", "L3"), fixtures_passed=9,
                fixtures_total=9, false_completions=0, evidence_ref="tests")
    return Capability(**{**base, **overrides})


class AGapBecomesAQuestion(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()

    def test_a_missing_capability_reaches_the_owners_queue(self):
        self.s.capability.assess(job_id="J1", requirements=owner_confirmed(),
                                 assessor=OWNER, needs=["render_xlsx"])
        queue = self.s.capability.awaiting_owner()
        self.assertEqual([p["name"] for p in queue], ["render_xlsx"])

    def test_the_job_is_still_refused_because_proposing_is_not_permission(self):
        assessment = self.s.capability.assess(
            job_id="J1", requirements=owner_confirmed(), assessor=OWNER,
            needs=["render_xlsx"])
        self.assertIs(assessment.verdict, CapabilityVerdict.REQUIRES_NEW_CAPABILITY)
        self.assertFalse(assessment.may_commit)

    def test_the_same_gap_is_not_proposed_twice(self):
        for job in ("J1", "J2", "J3"):
            self.s.capability.assess(job_id=job, requirements=owner_confirmed(),
                                     assessor=OWNER, needs=["render_xlsx"])
        self.assertEqual(len(self.s.capability.proposals("render_xlsx")), 1)

    def test_re_proposing_a_decided_capability_is_refused(self):
        """Asking again until the answer changes is how a denial wears down."""
        self.s.capability.propose(name="render_pdf",
                                  covers=frozenset({"render_pdf"}), why="gap")
        self.s.capability.decide(name="render_pdf", decision=Reg.DENIED,
                                 owner_identity=OWNER, why="no")
        with self.assertRaises(FailClosed) as caught:
            self.s.capability.propose(name="render_pdf",
                                      covers=frozenset({"render_pdf"}), why="again")
        self.assertIn("already has an owner decision", str(caught.exception))

    def test_an_unproposed_capability_may_not_be_developed(self):
        self.assertEqual(self.s.capability.may_develop("anything")[0], False)

    def test_silence_is_not_consent(self):
        self.s.capability.propose(name="x", covers=frozenset({"x"}), why="gap")
        allowed, why = self.s.capability.may_develop("x")
        self.assertFalse(allowed)
        self.assertIn("awaiting an owner decision", why)


class OnlyTheOwnerAnswers(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.s.capability.propose(name="render_xlsx",
                                  covers=frozenset({"render_xlsx"}), why="gap")

    def test_solvent_cannot_answer_its_own_question(self):
        for identity in ("capability", "worker:xlsx", "memory:learning", "qc",
                         "client:acme", "execution", "runtime"):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed):
                    self.s.capability.decide(name="render_xlsx",
                                             decision=Reg.APPROVED,
                                             owner_identity=identity, why="self")

    def test_an_answer_that_is_not_one_of_the_three_is_refused(self):
        with self.assertRaises(FailClosed):
            self.s.capability.decide(name="render_xlsx", decision="MAYBE",
                                     owner_identity=OWNER, why="hedging")

    def test_deciding_something_nobody_proposed_is_refused(self):
        with self.assertRaises(FailClosed):
            self.s.capability.decide(name="never_asked", decision=Reg.APPROVED,
                                     owner_identity=OWNER, why="?")

    def test_the_decision_is_on_the_record(self):
        self.s.capability.decide(name="render_xlsx", decision=Reg.LIMITED,
                                 owner_identity=OWNER, why="show me first",
                                 scope="fixtures only")
        events = [e for e in self.s.audit.events()
                  if e["event"] == "capability.decided"]
        self.assertEqual(len(events), 1)
        self.assertIn("LIMITED", events[0]["result"])


class Denied(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.s.capability.propose(name="render_pdf",
                                  covers=frozenset({"render_pdf"}), why="gap")
        self.s.capability.decide(name="render_pdf", decision=Reg.DENIED,
                                 owner_identity=OWNER, why="not now")

    def test_nothing_may_be_developed(self):
        allowed, why = self.s.capability.may_develop("render_pdf")
        self.assertFalse(allowed)
        self.assertIn("denied", why)

    def test_it_cannot_be_registered_even_unproven(self):
        with self.assertRaises(FailClosed) as caught:
            self.s.capability.register(
                Capability(name="render_pdf", covers=frozenset({"render_pdf"})),
                owner_identity=OWNER)
        self.assertIn("denied", str(caught.exception))

    def test_it_cannot_be_registered_as_proven(self):
        with self.assertRaises(FailClosed):
            self.s.capability.register(evidence("render_pdf"), owner_identity=OWNER)

    def test_it_cannot_be_deployed(self):
        self.assertFalse(self.s.capability.may_deploy("render_pdf")[0])

    def test_the_owner_may_change_their_mind_on_the_record(self):
        """Denial binds Solvent, not the owner. Reversing it leaves a trail."""
        self.s.capability.decide(name="render_pdf", decision=Reg.APPROVED,
                                 owner_identity=OWNER, why="reconsidered")
        self.assertTrue(self.s.capability.may_develop("render_pdf")[0])
        decisions = [p for p in self.s.capability.proposals("render_pdf")
                     if p["kind"] == "DECIDED"]
        self.assertEqual([d["decision"] for d in decisions],
                         [Reg.DENIED, Reg.APPROVED])


class Limited(unittest.TestCase):
    """Permission to build is not permission to use on a client."""

    def setUp(self):
        self.s = Solvent()
        self.s.capability.propose(name="draft_email",
                                  covers=frozenset({"draft_email"}), why="gap")
        self.s.capability.decide(name="draft_email", decision=Reg.LIMITED,
                                 owner_identity=OWNER, why="build it, show me",
                                 scope="local fixtures only, no client contact")

    def test_development_is_permitted(self):
        allowed, why = self.s.capability.may_develop("draft_email")
        self.assertTrue(allowed)
        self.assertIn("fixtures only", why)

    def test_deployment_is_refused_even_with_full_evidence(self):
        self.s.capability.register(evidence("draft_email"), owner_identity=OWNER)
        allowed, why = self.s.capability.may_deploy("draft_email")
        self.assertFalse(allowed)
        self.assertIn("separate owner decision", why)

    def test_the_owner_may_lift_the_limit(self):
        self.s.capability.register(evidence("draft_email"), owner_identity=OWNER)
        self.s.capability.decide(name="draft_email", decision=Reg.APPROVED,
                                 owner_identity=OWNER, why="tested, happy")
        self.assertTrue(self.s.capability.may_deploy("draft_email")[0])

    def test_the_scope_is_recorded_not_interpreted(self):
        """Prose the owner wrote is shown to a human, never parsed into rights."""
        import pathlib
        source = pathlib.Path("solvent/capability.py").read_text()
        block = source[source.index("def decide("):source.index("def development_decision(")]
        for parsing in ("scope.split", "scope.lower", 'in scope', "eval(", "re."):
            self.assertNotIn(parsing, block, f"scope is being interpreted: {parsing}")


class ApprovalIsNotEvidence(unittest.TestCase):
    """The hardest rule: the owner said try, not the owner said it works."""

    def setUp(self):
        self.s = Solvent()
        self.s.capability.propose(name="render_xlsx",
                                  covers=frozenset({"render_xlsx"}), why="gap")
        self.s.capability.decide(name="render_xlsx", decision=Reg.APPROVED,
                                 owner_identity=OWNER, why="try it")

    def test_a_failed_capability_cannot_be_called_proven(self):
        weak = evidence("render_xlsx", fixtures_passed=2, fixtures_total=3,
                        proven_levels=("L1",), false_completions=1)
        with self.assertRaises(FailClosed) as caught:
            self.s.capability.register(weak, owner_identity=OWNER)
        self.assertIn("needs evidence", str(caught.exception))

    def test_one_false_completion_blocks_it_whatever_the_pass_rate(self):
        many = evidence("render_xlsx", fixtures_passed=500, fixtures_total=500,
                        false_completions=1)
        with self.assertRaises(FailClosed):
            self.s.capability.register(many, owner_identity=OWNER)

    def test_it_may_be_registered_honestly_as_unproven(self):
        self.s.capability.register(
            Capability(name="render_xlsx", covers=frozenset({"render_xlsx"})),
            owner_identity=OWNER)
        self.assertFalse(self.s.capability.may_deploy("render_xlsx")[0])

    def test_a_capability_that_earns_it_is_registered_and_deployable(self):
        self.s.capability.register(evidence("render_xlsx"), owner_identity=OWNER)
        allowed, why = self.s.capability.may_deploy("render_xlsx")
        self.assertTrue(allowed, why)

    def test_approval_alone_does_not_make_it_deployable(self):
        allowed, why = self.s.capability.may_deploy("render_xlsx")
        self.assertFalse(allowed)
        self.assertIn("not evidence that it works", why)

    def test_an_owner_registered_capability_needs_no_proposal(self):
        """A capability the owner vouches for directly is their own judgement.

        The evidence requirement exists because Solvent would otherwise grade
        the thing it was asked to build. It does not apply to work the owner
        did not ask Solvent to do.
        """
        self.s.capability.register(
            Capability(name="spreadsheet", covers=frozenset({"spreadsheet"}),
                       proven=True),
            owner_identity=OWNER)
        self.assertIn("spreadsheet",
                      [c.name for c in self.s.capability.capabilities()])


class ProposalsAreHistory(unittest.TestCase):
    """A denial that can be edited into an approval is not a denial."""

    def setUp(self):
        import pathlib as _p
        import tempfile as _t

        self.db = str(_p.Path(_t.mkdtemp()) / "s.db")
        s = Solvent(self.db)
        s.capability.propose(name="x", covers=frozenset({"x"}), why="gap")
        s.capability.decide(name="x", decision=Reg.DENIED, owner_identity=OWNER,
                            why="no")
        s.store.close()

    def attempt(self, sql):
        """Run SQL through a raw connection, bypassing every authority wrapper."""
        import sqlite3

        conn = sqlite3.connect(self.db)
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def test_a_decision_cannot_be_rewritten(self):
        import sqlite3

        with self.assertRaises(sqlite3.Error) as caught:
            self.attempt("UPDATE capability_proposals SET decision='APPROVED'")
        self.assertIn("append-only", str(caught.exception))

    def test_a_decision_cannot_be_deleted(self):
        import sqlite3

        with self.assertRaises(sqlite3.Error) as caught:
            self.attempt("DELETE FROM capability_proposals")
        self.assertIn("append-only", str(caught.exception))

    def test_the_denial_survives_the_attempts(self):
        for sql in ("UPDATE capability_proposals SET decision='APPROVED'",
                    "DELETE FROM capability_proposals"):
            try:
                self.attempt(sql)
            except Exception:
                pass
        s = Solvent(self.db)
        self.assertEqual(s.capability.development_decision("x")[0], Reg.DENIED)


if __name__ == "__main__":
    unittest.main()
