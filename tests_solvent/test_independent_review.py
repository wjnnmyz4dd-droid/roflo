"""T2: a second opinion, and whether it is really a second one.

The architecture contract asks C_HIGH for "T1 + **T2 with verifier ≠
executor**", and the verification audit says why the wording is strict: a
review by the same instance, re-reading the same document, is not
independent — it can be argued into approving.

So the tests that matter here are not "does a T2 row appear". They are:

* does the reviewer **catch a wrong figure** the first verifier would have
  passed? (If not, it is a parse test wearing a tier.)
* can a T2 row be written **without** the thing that makes it trustworthy —
  a signature for a person, separate authorship for a library?
* is the job stopped **before** the money is spent when nobody can review it?
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from tests_solvent.fixtures import give_an_issuer
from solvent import independent, xlsx
from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.errors import FailClosed
from solvent.harness import (
    OWNER, Solvent, owner_promotions, promote_capability, run_business_cycle,
)
from solvent.types import (
    ActionClass, ConsequenceTier, Criticality, Requirement, RequirementSource,
    VerificationTier,
)

HIGH_VALUE = 200_000   # $2,000 — above the C_HIGH threshold
LOW_VALUE = 50_000     # $500   — C_MED, T1 is enough


def req(rid, check, params=None):
    return Requirement(
        id=rid, text=check, source=RequirementSource.CLIENT_CONFIRMED_STRUCTURED,
        acceptance=check, check=check, params=params or {},
        criticality=Criticality.MANDATORY)


AGREED = [
    req("R1", "require_columns", {"columns": ["Item", "Units", "Rate"]}),
    req("R2", "trim_whitespace", {"columns": ["Item"]}),
    req("R3", "compute_column", {"into": "Amount", "expression": "product",
                                 "using": ["Units", "Rate"]}),
]


class Base(unittest.TestCase):

    value = HIGH_VALUE

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(str(self.dir / "t2.db"))
        give_an_issuer(self.s)
        self.addCleanup(self.close)
        for name in owner_promotions():
            promote_capability(self.s, name, owner_identity=OWNER,
                               why="test: cleared for the T2 suite")
        self.s.discovery.register_source(
            FixtureSource("board", [{
                "ref": "t2", "title": "A workbook worth reviewing",
                "quoted_cents": self.value, "needs": ["spreadsheet-automation"],
                "project_state": "CT", "client_ref": "client:acme",
                "body": "compute the amounts", "deliverables": []}]),
            owner_identity=OWNER, readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="test fixture")
        self.s.policy.amend({
            "discovery": {"approved_sources": ["board"]},
            "financial": {"max_job_spend_cents": 500_000,
                          "owner_approval_above_cents": 10_000_000},
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True},
        }, OWNER, "test: a high-value job the owner does not have to approve")
        self.source = self.dir / "client.xlsx"
        self.source.write_bytes(xlsx.write_workbook([xlsx.SheetOut(
            name="Data", rows=[["Item", "Units", "Rate"],
                               ["  Consulting ", 4, "25.50"],
                               ["Support", 2, "100.00"]])]))

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def cycle(self, **kwargs):
        defaults = dict(
            source_name="board", source_file=str(self.source),
            workdir=self.box.name, capability="spreadsheet-automation",
            agreed_requirements=AGREED, owner_accepts_quote=True,
            simulate_payment=True)
        return run_business_cycle(self.s, **{**defaults, **kwargs})

    def t2_rows(self):
        return self.s.store.raw_readonly(
            "SELECT * FROM verification_evidence WHERE tier = ?",
            (VerificationTier.T2_INDEPENDENT_REVIEW.value,))


class TheTierIsCheckedBeforeTheMoney(Base):

    def test_a_high_value_job_names_the_tier_it_needs(self):
        cycle = self.cycle()
        self.assertEqual(cycle.required_tier,
                         VerificationTier.T2_INDEPENDENT_REVIEW.value)
        self.assertTrue(cycle.tier_reachable, cycle.tier_detail)

    def test_the_check_happens_before_the_budget_is_granted(self):
        """Order matters: the point is not to spend first and discover after."""
        from solvent.harness import CYCLE_STAGES

        stages = list(CYCLE_STAGES)
        self.assertLess(stages.index("verification-eligibility"),
                        stages.index("budget"))
        self.assertLess(stages.index("verification-eligibility"),
                        stages.index("execute"))

    def test_a_job_nobody_can_review_stops_before_any_work(self):
        """The whole reason this check exists.

        With no signing key and no independently authored reader for the
        format, a C_HIGH job cannot reach the tier it needs. It should be
        declined while it is still a quote.
        """
        import solvent.independent as module

        original = module._DIFFERENTIAL_READERS
        module._DIFFERENTIAL_READERS = {}
        self.addCleanup(setattr, module, "_DIFFERENTIAL_READERS", original)

        cycle = self.cycle()
        self.assertEqual(cycle.stopped_at, "verification-eligibility")
        self.assertFalse(cycle.tier_reachable)
        self.assertIn("T2_INDEPENDENT_REVIEW", cycle.why_stopped)
        self.assertEqual(
            self.s.store.raw_readonly("SELECT * FROM budget_grants"), [],
            "a budget was granted for work that could never be delivered")
        self.assertEqual(
            self.s.orchestrator.artifacts(cycle.job_id, role="DELIVERABLE"), [],
            "work was produced for a job that could never be delivered")

    def test_a_low_value_job_needs_no_second_opinion(self):
        """T1 is the whole requirement below the threshold, and adding a
        ceremonial second pass would be the theatre the contract warns of."""
        self.s.store.close()
        Base.value = LOW_VALUE
        self.addCleanup(setattr, Base, "value", HIGH_VALUE)
        self.setUp()
        cycle = self.cycle()
        self.assertEqual(cycle.required_tier,
                         VerificationTier.T1_DETERMINISTIC.value)
        self.assertNotIn("independent-review", cycle.stages)
        self.assertEqual(self.t2_rows(), [])
        self.assertTrue(cycle.delivered, cycle.why_stopped)


class TheSecondOpinionIsAboutTheFigures(Base):
    """The test that decides whether this is evidence or a parse check."""

    def test_a_high_value_job_is_delivered_with_recorded_t2_evidence(self):
        cycle = self.cycle()
        self.assertTrue(cycle.completed,
                        f"{cycle.stopped_at}: {cycle.why_stopped}")
        self.assertIn("independent-review", cycle.stages)
        rows = self.t2_rows()
        self.assertTrue(rows)
        self.assertTrue(all(r["verdict"] == "PASS" for r in rows))

    def test_the_review_says_what_it_recomputed(self):
        self.cycle()
        detail = str(self.t2_rows()[0]["raw_output"])
        self.assertIn("independently recomputed", detail)
        self.assertIn("Amount", detail,
                      "the review did not name a figure it checked, so it is "
                      "a parse test wearing a tier")

    def test_a_wrong_figure_is_caught_by_the_independent_reader(self):
        """The negative control the whole module rests on.

        The deliverable is altered after the worker wrote it so that one
        Amount no longer equals Units x Rate. Solvent's own verifier is not
        consulted; openpyxl recomputes from the client's source and must
        disagree.
        """
        cycle = self.cycle()
        artifact = self.s.orchestrator.current_deliverable(cycle.job_id)
        book = xlsx.read_workbook(pathlib.Path(artifact.path).read_bytes())
        rows = [[cell.value for cell in row] for row in book.first.rows]
        amount = rows[0].index("Amount")
        rows[1][amount] = float(rows[1][amount]) + 1_000      # a wrong total
        tampered = self.dir / "tampered.xlsx"
        tampered.write_bytes(xlsx.write_workbook([
            xlsx.SheetOut(name="Data", rows=rows)]))

        class Fake:
            path = str(tampered)
            digest = "0" * 64
            id = "art_fake"

        with self.assertRaises(FailClosed) as caught:
            independent.differential_review(
                self.s.audit, job_id=cycle.job_id, source=str(self.source),
                artifact=Fake(), requirement_ids=["R3"],
                consequence=ConsequenceTier.C_HIGH, requirements=AGREED)
        message = str(caught.exception)
        self.assertIn("disagrees with the deliverable", message)
        self.assertIn("Amount", message)

    def test_a_brief_with_nothing_recomputable_is_refused_not_passed(self):
        """"It parses" is not a review, and saying so is the point."""
        cycle = self.cycle()
        artifact = self.s.orchestrator.current_deliverable(cycle.job_id)
        with self.assertRaises(FailClosed) as caught:
            independent.differential_review(
                self.s.audit, job_id=cycle.job_id, source=str(self.source),
                artifact=artifact, requirement_ids=["R1"],
                consequence=ConsequenceTier.C_HIGH,
                requirements=[req("R1", "trim_whitespace",
                                  {"columns": ["Item"]})])
        self.assertIn("Review by a person is required",
                      str(caught.exception))

    def test_a_format_with_no_independent_reader_is_refused(self):
        cycle = self.cycle()

        class Csv:
            path = "/tmp/whatever.csv"
            digest = "0" * 64
            id = "art_csv"

        with self.assertRaises(FailClosed) as caught:
            independent.differential_review(
                self.s.audit, job_id=cycle.job_id, source=str(self.source),
                artifact=Csv(), requirement_ids=["R1"],
                consequence=ConsequenceTier.C_HIGH, requirements=AGREED)
        self.assertIn("no independently authored reader for .csv",
                      str(caught.exception))


class APersonsReviewNeedsTheirSignature(Base):

    def test_an_unsigned_review_is_refused(self):
        cycle = self.cycle()
        artifact = self.s.orchestrator.current_deliverable(cycle.job_id)
        with self.assertRaises(FailClosed) as caught:
            independent.owner_review(
                self.s.audit, job_id=cycle.job_id, approval=None,
                owner_channel=self.s.owner, reviewer_identity=OWNER,
                requirement_ids=["R1"], artifact=artifact,
                consequence=ConsequenceTier.C_HIGH)
        self.assertIn("must be signed", str(caught.exception))

    def test_a_review_signed_for_a_different_artifact_is_refused(self):
        """An approval that verifies perfectly, for the wrong file."""
        import os
        import secrets

        from solvent.owner import OwnerChannel

        os.environ["SOLVENT_OWNER_KEY"] = secrets.token_hex(32)
        self.addCleanup(os.environ.pop, "SOLVENT_OWNER_KEY", None)
        channel = OwnerChannel(self.s.store, self.s.audit, self.s.policy)
        if not channel.available:
            self.skipTest(f"no owner channel: {channel.key_refusal}")

        cycle = self.cycle()
        artifact = self.s.orchestrator.current_deliverable(cycle.job_id)
        approval = channel.issue(
            owner_identity=OWNER,
            subject=independent.REVIEW_SUBJECT.format(digest="a" * 64),
            action_class=ActionClass.C0_INTERNAL_READ, job_id=cycle.job_id)
        with self.assertRaises(FailClosed) as caught:
            independent.owner_review(
                self.s.audit, job_id=cycle.job_id, approval=approval,
                owner_channel=channel, reviewer_identity=OWNER,
                requirement_ids=["R1"], artifact=artifact,
                consequence=ConsequenceTier.C_HIGH)
        self.assertIn("not this artifact", str(caught.exception))

    def test_the_executor_cannot_review_its_own_work(self):
        cycle = self.cycle()
        artifact = self.s.orchestrator.current_deliverable(cycle.job_id)
        with self.assertRaises(FailClosed) as caught:
            self.s.audit.record_independent_review(
                subject_ref=cycle.job_id, requirement_id="R1",
                artifact_digest=artifact.digest, artifact_id=artifact.id,
                reviewer_identity="worker",
                consequence=ConsequenceTier.C_HIGH,
                method="m", detail="d")
        self.assertIn("cannot also be the independent review",
                      str(caught.exception))

    def test_review_evidence_dies_with_the_artifact_it_reviewed(self):
        """A correction invalidates a review, like every other kind of pass."""
        cycle = self.cycle()
        artifact = self.s.orchestrator.current_deliverable(cycle.job_id)
        self.s.audit.record_independent_review(
            subject_ref=cycle.job_id, requirement_id="R1",
            artifact_digest="b" * 64, artifact_id=artifact.id,
            reviewer_identity="reviewer:someone",
            consequence=ConsequenceTier.C_HIGH, method="m", detail="d")
        passes = self.s.audit.passes_for_artifact(cycle.job_id, artifact.digest)
        self.assertNotIn("b" * 64,
                         [p.get("artifact_digest") for p in passes.values()])

    def test_evidence_without_a_requirement_or_digest_is_refused(self):
        for missing in ("requirement_id", "artifact_digest"):
            fields = dict(subject_ref="job_x", requirement_id="R1",
                          artifact_digest="c" * 64, artifact_id="a",
                          reviewer_identity="reviewer:someone",
                          consequence=ConsequenceTier.C_HIGH,
                          method="m", detail="d")
            fields[missing] = ""
            with self.subTest(missing=missing), self.assertRaises(FailClosed):
                self.s.audit.record_independent_review(**fields)


class WhatThisInstallationCanActuallyProduce(unittest.TestCase):

    def test_it_reports_the_producers_rather_than_assuming_them(self):
        report = independent.available(extension="xlsx", owner_channel=None)
        self.assertTrue(report.any_usable)
        self.assertTrue(report.automated)
        self.assertIn("openpyxl", report.summary())

    def test_a_format_with_no_independent_reader_says_why(self):
        report = independent.available(extension="csv", owner_channel=None)
        self.assertFalse(report.automated)
        self.assertIn("reading its own output twice", report.summary())

    def test_owner_review_is_reported_as_needing_a_person(self):
        report = independent.available(extension="csv", owner_channel=None)
        owner = next(p for p in report.producers if p.name == "owner review")
        self.assertTrue(owner.needs_a_person)
        self.assertFalse(owner.usable, "no key is configured in this test")


if __name__ == "__main__":
    unittest.main()


class AnApprovalIsSingleUseUnderConcurrency(unittest.TestCase):
    """The race between verify and consume.

    ``verify`` reads ``used_at`` and returns; the caller then acts and claims
    the nonce. Two callers holding one approval can both pass ``verify``
    before either claims it, so the claim itself has to be what decides.
    """

    def setUp(self):
        import os
        import secrets

        from solvent.owner import OwnerChannel

        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/race.db")
        give_an_issuer(self.s)
        self.addCleanup(self.s.store.close)
        os.environ["SOLVENT_OWNER_KEY"] = secrets.token_hex(32)
        self.addCleanup(os.environ.pop, "SOLVENT_OWNER_KEY", None)
        self.channel = OwnerChannel(self.s.store, self.s.audit, self.s.policy)
        if not self.channel.available:
            self.skipTest(self.channel.key_refusal)

    def approval(self):
        return self.channel.issue(
            owner_identity=OWNER, subject="spend on one job",
            action_class=ActionClass.C0_INTERNAL_READ, job_id="job_one")

    def test_a_second_claim_is_refused(self):
        approval = self.approval()
        self.channel.consume(approval)
        with self.assertRaises(FailClosed) as caught:
            self.channel.consume(approval)
        self.assertIn("already been used", str(caught.exception))

    def test_two_threads_holding_one_approval_yield_exactly_one_winner(self):
        import threading

        approval = self.approval()
        barrier = threading.Barrier(8)
        wins, losses = [], []
        lock = threading.Lock()

        # Two barriers, not one. With a single barrier each thread can run
        # verify *and* consume before the next thread starts, so verify sees
        # used_at already set and refuses -- the window never opens and the
        # test passes whether or not the claim is atomic. Everyone verifies,
        # then everyone claims: that is the interleaving being defended
        # against, and it has to be forced rather than hoped for.
        claim_gate = threading.Barrier(8)

        def attempt():
            barrier.wait()
            problem = self.channel.verify(
                approval, action_class=ActionClass.C0_INTERNAL_READ,
                job_id="job_one", amount_cents=0)
            claim_gate.wait()
            try:
                if problem:
                    raise FailClosed(problem)
                self.channel.consume(approval)
            except Exception as exc:
                with lock:
                    losses.append(str(exc))
            else:
                with lock:
                    wins.append(True)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(
            len(wins), 1,
            f"{len(wins)} of 8 threads were authorised by one single-use "
            f"approval; losses were {losses}")
        self.assertEqual(len(losses), 7)

    def test_the_refusal_is_recorded(self):
        approval = self.approval()
        self.channel.consume(approval)
        with self.assertRaises(FailClosed):
            self.channel.consume(approval)
        events = [r for r in self.s.store.raw_readonly("SELECT * FROM audit_log")
                  if r["event"] == "owner.approval_replay_refused"]
        self.assertTrue(events, "a refused replay left no trace")
