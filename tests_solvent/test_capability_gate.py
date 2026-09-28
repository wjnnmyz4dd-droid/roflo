"""Delivery asks whether the capability that produced a file may be sold.

Nothing asked. A cold-start Solvent with an empty registry executed a CSV job,
verified it against every committed requirement, and delivered it — while
``may_deploy`` said no and no code path consulted it. The owner's promotion
decision, the LIMITED "development and testing only" grant, the registry and
the Skills Lab's whole promotion boundary were bypassable by running a job.

That made three separate laws advisory at once:

    Capability ≠ conformance.
    Conformance ≠ profitability.
    Profitability ≠ permission.

The gate is one more question at the chokepoint that already answers *may this
be delivered?*, asked of the registry, which owns the answer. These tests are
about the question being asked at all, and about what happens on each answer.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent.capability import Capability
from solvent.checks import checks_for
from solvent.cli import main as cli_main
from solvent.errors import FailClosed
from solvent.harness import (CSV_PROMOTION, OWNER, Solvent, _owner_promoted,
                             provision_capability, run_csv_job)
from tests_solvent import fixtures_csv as fx


def unprovisioned_job(solvent, **kwargs):
    """A CSV job on a Solvent whose registry has never been provisioned."""
    source, work = fx.workspace(fx.DUPLICATES)
    return run_csv_job(source=source, requirements=list(fx.SIMPLE), workdir=work,
                       solvent=solvent, client_id="client:gate",
                       title="gate test", **kwargs), source


class AColdStartDoesNotSellWorkNobodyCleared(unittest.TestCase):
    """The defect, reproduced, and the behaviour that replaced it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")

    def test_a_fresh_solvent_has_no_capabilities(self):
        """The starting state this whole file is about. Stated, not assumed."""
        solvent = Solvent(self.db)
        self.assertEqual([c.name for c in solvent.capability.capabilities()], [])
        permitted, why = solvent.capability.may_deploy("csv-cleanup")
        self.assertFalse(permitted)
        self.assertIn("not been proposed", why)

    def test_work_from_an_unprovisioned_registry_is_not_delivered(self):
        solvent = Solvent(self.db)
        # The fixture would normally provision; this is the cold start.
        report, _ = unprovisioned_job(solvent, provision=False or None,
                                      configure_fixture_policy=False)
        self.assertFalse(report.delivered)

    def test_the_refusal_names_the_capability_and_the_reason(self):
        solvent = Solvent(self.db)
        source, work = fx.workspace(fx.DUPLICATES)
        solvent.policy.amend({
            "egress": {"allowlist": [{"host": "client.example.com",
                                      "max_privacy": "CLIENT_CONFIDENTIAL",
                                      "classes": ["C2_EXTERNAL_COMMUNICATION"]}],
                       "simulation_only": True}},
            OWNER, "egress configured so the only thing left is permission")
        report = run_csv_job(source=source, requirements=list(fx.SIMPLE),
                             workdir=work, solvent=solvent,
                             client_id="client:gate", title="gate",
                             configure_fixture_policy=False)
        self.assertFalse(report.delivered,
                         "unpromoted work was delivered once egress allowed it")
        self.assertIn("csv-cleanup/1.0", report.escalated)
        self.assertIn("may not be delivered", report.escalated)

    def test_the_work_itself_was_still_done_correctly(self):
        """Refusing to *sell* is not refusing to *work*. The distinction is the
        point: the artifact is correct and verified, and permission is separate."""
        solvent = Solvent(self.db)
        source, work = fx.workspace(fx.DUPLICATES)
        report = run_csv_job(source=source, requirements=list(fx.SIMPLE),
                             workdir=work, solvent=solvent,
                             client_id="client:gate", title="gate",
                             configure_fixture_policy=False)
        deliverable = solvent.orchestrator.current_deliverable(report.job_id)
        self.assertIsNotNone(deliverable, "no artifact was produced at all")
        self.assertEqual(deliverable.capability_version, "csv-cleanup/1.0")


class ProvisioningIsWhatMakesItDeliverable(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(pathlib.Path(self.dir.name) / "solvent.db")

    def test_the_owner_command_makes_a_refused_job_deliverable(self):
        """End to end through the real command, not a test helper."""
        before = Solvent(self.db)
        report, _ = unprovisioned_job(before, configure_fixture_policy=False)
        self.assertFalse(report.delivered)
        before.store.close()

        self.assertEqual(cli_main(["setup", "capability", "--db", self.db,
                                   ]), 0)

        after = Solvent(self.db)
        permitted, why = after.capability.may_deploy("csv-cleanup")
        self.assertTrue(permitted, why)
        report, _ = unprovisioned_job(after, configure_fixture_policy=True)
        self.assertTrue(report.delivered, report.escalated)

    def test_the_command_records_a_decision_not_just_a_registration(self):
        """`may_deploy` asks `may_develop`, which needs the owner to have
        answered a proposal. Registering alone left every job refused with
        "csv-cleanup has not been proposed", so an owner who followed the setup
        guide exactly ended up with a Solvent that could not deliver."""
        self.assertEqual(cli_main(["setup", "capability", "--db", self.db,
                                   ]), 0)
        solvent = Solvent(self.db)
        decision, _ = solvent.capability.development_decision("csv-cleanup")
        self.assertEqual(decision, solvent.capability.APPROVED)
        self.assertTrue(solvent.capability.proposals("csv-cleanup"))

    def test_provisioning_is_durable_across_a_restart(self):
        provision_capability(Solvent(self.db), CSV_PROMOTION,
                             owner_identity=OWNER)
        restarted = Solvent(self.db)
        permitted, why = restarted.capability.may_deploy("csv-cleanup")
        self.assertTrue(permitted, why)
        report, _ = unprovisioned_job(restarted, configure_fixture_policy=True)
        self.assertTrue(report.delivered, report.escalated)

    def test_provisioning_twice_is_not_an_error(self):
        solvent = Solvent(self.db)
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)
        proven = [c for c in solvent.capability.capabilities() if c.proven]
        self.assertEqual(len(proven), 1)

    def test_provisioning_re_verifies_rather_than_trusting_the_last_run(self):
        solvent = Solvent(self.db)
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)
        solvent.capability.revoke_verifier(
            verifier_ref=solvent.capability.verifier_certification(
                "solvent.csvverify.run", "csv-cleanup/1.0")["verifier_ref"],
            capability_version="csv-cleanup/1.0",
            why="the verifier was found wrong", decided_by=OWNER)
        fresh = Solvent(self.db)
        with self.assertRaises(FailClosed):
            provision_capability(fresh, CSV_PROMOTION, owner_identity=OWNER)


class EachAnswerFromTheRegistry(unittest.TestCase):
    """One test per way the registry can say no, and the one way it says yes."""

    def solvent(self):
        return Solvent()

    def test_limited_means_build_and_test_and_not_sell(self):
        solvent = self.solvent()
        covers = frozenset(checks_for("report-builder"))
        solvent.capability.propose(name="report-builder", covers=covers,
                                   why="a client sent a report request",
                                   requested_by=OWNER)
        solvent.capability.decide(name="report-builder",
                                  decision=solvent.capability.LIMITED,
                                  owner_identity=OWNER, why="not for clients yet",
                                  scope="development and testing")
        permitted, why = solvent.capability.may_deploy("report-builder")
        self.assertFalse(permitted)
        self.assertIn("development and testing only", why)

    def test_denied_means_no(self):
        solvent = self.solvent()
        solvent.capability.propose(name="pdf-designer", covers=frozenset({"x"}),
                                   why="asked for", requested_by=OWNER)
        solvent.capability.decide(name="pdf-designer",
                                  decision=solvent.capability.DENIED,
                                  owner_identity=OWNER, why="out of scope")
        self.assertFalse(solvent.capability.may_deploy("pdf-designer")[0])

    def test_approved_but_unregistered_is_still_not_proven(self):
        solvent = self.solvent()
        solvent.capability.propose(name="pdf-designer", covers=frozenset({"x"}),
                                   why="asked for", requested_by=OWNER)
        solvent.capability.decide(name="pdf-designer",
                                  decision=solvent.capability.APPROVED,
                                  owner_identity=OWNER, why="go ahead")
        permitted, why = solvent.capability.may_deploy("pdf-designer")
        self.assertFalse(permitted)
        self.assertIn("not yet registered as proven", why)

    def test_a_direct_owner_registration_is_itself_the_decision(self):
        """`register` documents this case — "their own judgement about their own
        business, and needs no fixture count" — and `may_deploy` used to refuse
        it for want of a proposal that by definition does not exist, so the
        documented path was a dead end."""
        solvent = self.solvent()
        solvent.capability.register(
            Capability(name="hand-made", covers=frozenset({"x"}),
                       version="hand-made/1.0", proven=True),
            owner_identity=OWNER)
        self.assertTrue(solvent.capability.may_deploy("hand-made")[0])
        developable, why = solvent.capability.may_develop("hand-made")
        self.assertTrue(developable)
        self.assertIn("registered directly by the owner", why)

    def test_a_non_owner_cannot_register_its_way_to_permission(self):
        solvent = self.solvent()
        with self.assertRaises(FailClosed):
            solvent.capability.register(
                Capability(name="self-made", covers=frozenset({"x"}),
                           version="self-made/1.0", proven=True),
                owner_identity="execution")
        self.assertFalse(solvent.capability.may_deploy("self-made")[0])


class TheVersionThatProducedTheFileIsTheVersionThatMustBeProven(unittest.TestCase):
    def test_a_different_version_is_not_covered_by_the_registration(self):
        """Evidence gathered against one version is not evidence for the next."""
        solvent = Solvent()
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)
        deliverable = type("A", (), {"capability_version": "csv-cleanup/2.0"})()
        permitted, why = solvent.orchestrator._capability_permitted(deliverable)
        self.assertFalse(permitted)
        self.assertIn("csv-cleanup/1.0", why)

    def test_an_unstamped_artifact_has_no_provenance_and_is_refused(self):
        solvent = Solvent()
        provision_capability(solvent, CSV_PROMOTION, owner_identity=OWNER)
        deliverable = type("A", (), {"capability_version": ""})()
        permitted, why = solvent.orchestrator._capability_permitted(deliverable)
        self.assertFalse(permitted)
        self.assertIn("records no capability version", why)

    def test_with_no_registry_wired_the_question_cannot_be_answered(self):
        """An unanswerable permission question refuses rather than assuming."""
        from solvent.orchestrator import JobOrchestrator

        solvent = Solvent()
        bare = JobOrchestrator(solvent.store, solvent.audit, solvent.policy,
                               solvent.governor, solvent.ledger)
        deliverable = type("A", (), {"capability_version": "csv-cleanup/1.0"})()
        permitted, why = bare._capability_permitted(deliverable)
        self.assertFalse(permitted)
        self.assertIn("no capability registry is wired", why)


class TheShippedConfiguration(unittest.TestCase):
    """What a real deployment promotes, asserted rather than assumed."""

    def test_csv_cleanup_is_the_only_promoted_capability(self):
        self.assertIsNotNone(_owner_promoted("csv-cleanup"))
        self.assertEqual(_owner_promoted("csv-cleanup").version, "csv-cleanup/1.0")

    def test_report_builder_is_not_promoted(self):
        self.assertIsNone(_owner_promoted("report-builder"),
                          "report-builder is technically certified and the "
                          "owner has not cleared it for client work")

    def test_a_configured_deployment_promotes_nothing_else(self):
        solvent = Solvent()
        source, work = fx.workspace(fx.DUPLICATES)
        run_csv_job(source=source, requirements=list(fx.SIMPLE), workdir=work,
                    solvent=solvent, client_id="c", title="t")
        proven = sorted(c.name for c in solvent.capability.capabilities()
                        if c.proven)
        self.assertEqual(proven, ["csv-cleanup"])

    def test_running_a_job_never_adds_a_capability(self):
        solvent = Solvent()
        source, work = fx.workspace(fx.DUPLICATES)
        run_csv_job(source=source, requirements=list(fx.SIMPLE), workdir=work,
                    solvent=solvent, client_id="c", title="one")
        before = {(c.name, c.version) for c in solvent.capability.capabilities()}
        source, work = fx.workspace(fx.DUPLICATES)
        run_csv_job(source=source, requirements=list(fx.SIMPLE), workdir=work,
                    solvent=solvent, client_id="c", title="two")
        self.assertEqual({(c.name, c.version)
                          for c in solvent.capability.capabilities()}, before)


class AVersionMustBelongToItsCapability(unittest.TestCase):
    """Found by reading the data model for states it permits and the business
    forbids: ``Capability(name="anything", version="csv-cleanup/1.0")``
    registered happily, at which point the version string stopped identifying
    the capability — which is exactly what the delivery gate reads it for."""

    def test_a_version_naming_a_different_capability_is_refused(self):
        solvent = Solvent()
        with self.assertRaises(FailClosed) as caught:
            solvent.capability.register(
                Capability(name="anything", covers=frozenset({"x"}),
                           version="csv-cleanup/1.0", proven=True),
                owner_identity=OWNER)
        self.assertIn("is not a version of", str(caught.exception))

    def test_a_version_that_is_the_bare_name_is_allowed(self):
        solvent = Solvent()
        solvent.capability.register(
            Capability(name="thing", covers=frozenset({"x"}), version="thing",
                       proven=True), owner_identity=OWNER)
        self.assertTrue(solvent.capability.may_deploy("thing")[0])

    def test_no_version_at_all_is_allowed_and_delivers_nothing(self):
        """A capability making no version claim is legitimate; an artifact
        stamped with one still cannot match it."""
        solvent = Solvent()
        solvent.capability.register(
            Capability(name="unversioned", covers=frozenset({"x"}), proven=True),
            owner_identity=OWNER)
        deliverable = type("A", (), {"capability_version": "unversioned/1.0"})()
        permitted, _ = solvent.orchestrator._capability_permitted(deliverable)
        self.assertFalse(permitted)

    def test_the_shipped_promotions_satisfy_the_rule(self):
        for capability in (CSV_PROMOTION,):
            with self.subTest(capability=capability.name):
                self.assertTrue(
                    capability.version.startswith(capability.name + "/"))
