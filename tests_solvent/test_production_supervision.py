"""Supervision as production actually wires it, not as a test can wire it.

The defect these tests exist to prevent was not a missing mechanism. The
incident recorder, the flight trace, the escalation path and the health
panel were all written, all tested, and all correct. What nothing checked
was whether production ever *called* them, and it did not: ``supervised``
had six callers and all six were in ``test_crash_matrix.py``. That suite
passed by supplying the integration it was meant to verify.

So nothing in this file may call :func:`supervised` directly. Every test
goes through :func:`run_business_cycle`, which is the function the CLI
calls, and asserts on what the system recorded afterwards. A test that
builds its own supervision proves only that supervision can be built.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from solvent import resilience as res
from solvent.harness import (PROCESSED_INTENT_RETENTION_DAYS, Solvent,
                             prune_processed_intents)
from tests_solvent import faults
from tests_solvent.test_business_cycle import Cycle

#: The seams on the delivery path. Each replaces a method a real authority
#: calls, so the failure arrives where a genuine one would.
DELIVERY_SEAMS = ("intake", "requirements", "artifact_registration",
                  "verification", "delivery_gate", "delivery", "ledger")


class ProductionInvokesSupervision(Cycle):
    """The wiring itself: does running a cycle the normal way record?"""

    def test_a_failed_cycle_records_an_incident(self):
        for seam in DELIVERY_SEAMS:
            with self.subTest(seam=seam):
                # A fresh system per seam. Reusing one would mean every seam
                # after the first ran against a shop that already had a job
                # stuck in it, and a cycle that stops early raises nothing --
                # which would make this pass for the wrong reason.
                self.setUp()
                with self.assertRaises(Exception):
                    with faults.inject(self.s, seam):
                        self.runCycle()
                self.assertEqual(
                    len(self.s.resilience.incidents(open_only=True)), 1,
                    f"a cycle that failed at {seam!r} recorded no incident; "
                    "production is not passing through supervision")

    def test_the_incident_names_the_component_and_the_error(self):
        with self.assertRaises(Exception):
            with faults.inject(self.s, "verification"):
                self.runCycle()
        incident = self.s.resilience.incidents(open_only=True)[0]
        self.assertEqual(incident["component"], "harness")
        self.assertEqual(incident["operation"], "business-cycle")
        self.assertEqual(incident["error_class"], "InjectedFault")

    def test_the_original_error_still_reaches_the_caller(self):
        """Recording a failure is not handling it."""
        with self.assertRaises(faults.InjectedFault):
            with faults.inject(self.s, "delivery"):
                self.runCycle()

    def test_a_clean_cycle_records_nothing(self):
        """Supervision must not invent incidents for work that succeeded."""
        cycle = self.runCycle()
        self.assertTrue(cycle.delivered)
        self.assertEqual(self.s.resilience.incidents(open_only=True), [])


class HealthReflectsWhatHappened(Cycle):
    """The panel reads the record rather than reporting health by default."""

    def test_a_failed_cycle_is_not_healthy(self):
        for seam in DELIVERY_SEAMS:
            with self.subTest(seam=seam):
                self.setUp()           # one fresh system per seam, as above
                with self.assertRaises(Exception):
                    with faults.inject(self.s, seam):
                        self.runCycle()
                finding = self.s.diagnostics.incidents()
                self.assertNotEqual(
                    finding.state, "HEALTHY",
                    f"after a real failure at {seam!r} the panel still reads "
                    f"{finding.state}: {finding.detail}")

    def test_an_untouched_system_is_healthy(self):
        """The inverse, so the check above cannot pass by always failing."""
        self.assertEqual(self.s.diagnostics.incidents().state, "HEALTHY")


class IncidentsSurviveRestart(Cycle):
    """An unresolved incident is not forgotten when the process goes away."""

    def test_an_open_incident_is_still_open_after_reopening_the_database(self):
        path = self.dir / "restart.db"
        first = Solvent(str(path))
        try:
            first.resilience.record_incident(
                component="harness", operation="business-cycle",
                error_class="InjectedFault", signature="restart probe",
                severity=res.S_SERIOUS, trigger="probe", symptoms="probe")
            open_before = len(first.resilience.incidents(open_only=True))
        finally:
            first.store.close()
        self.assertEqual(open_before, 1)

        second = Solvent(str(path))
        try:
            still_open = second.resilience.incidents(open_only=True)
            self.assertEqual(
                len(still_open), 1,
                "an unresolved incident did not survive a restart")
            self.assertEqual(second.diagnostics.incidents().state, "DEGRADED")
        finally:
            second.store.close()


class RecoveryOutcomesAreRecorded(Cycle):
    """A contained failure and a recovered one are different states."""

    def setUp(self):
        super().setUp()
        with self.assertRaises(Exception):
            with faults.inject(self.s, "verification"):
                self.runCycle()
        self.incident = self.s.resilience.incidents(open_only=True)[0]["id"]

    def test_containment_is_not_recovery(self):
        incident = self.s.resilience.incident(self.incident)
        self.assertNotEqual(
            incident["status"], res.RECOVERED,
            "a failure that was merely contained must not read as recovered")

    def test_a_recorded_recovery_moves_the_state(self):
        self.s.resilience.record_recovery(
            self.incident, action="retried the cycle", result="the next "
            "cycle delivered")
        self.assertNotEqual(
            self.s.resilience.incident(self.incident)["status"], res.DETECTED)

    def test_a_later_cycle_does_not_close_the_incident_by_itself(self):
        """The business carrying on is not the same as the incident closing.

        Whether the next cycle delivers is beside the point and deliberately
        not asserted here: what matters is that nothing silently marks the
        earlier failure resolved just because time passed.
        """
        self.runCycle()
        self.assertIn(self.incident,
                      [i["id"] for i in self.s.resilience.incidents(open_only=True)])


class OneFailureIsOneConversation(Cycle):
    """Repeated identical failures must not become repeated alerts."""

    def test_the_same_failure_twice_shares_one_dedupe_key(self):
        keys = []
        original = self.s.notifier.notify

        def capture(**kwargs):
            keys.append(kwargs.get("dedupe_key"))
            return original(**kwargs)

        self.s.notifier.notify = capture
        for _ in range(3):
            with self.assertRaises(Exception):
                with faults.inject(self.s, "verification"):
                    self.runCycle()
            # Re-arm on a clean shop so each round is a genuine repeat of the
            # same failure rather than a different one.
            self.setUp()
            original = self.s.notifier.notify
            self.s.notifier.notify = capture
        incident_keys = [k for k in keys if k and k.startswith("incident:")]
        self.assertTrue(incident_keys, "no incident notification was routed")
        self.assertEqual(
            len(set(incident_keys)), 1,
            f"three identical failures produced {len(set(incident_keys))} "
            "conversations; they should share one")


class WhenTheRecorderItselfFails(Cycle):
    """An unrecordable failure must not be an invisible one."""

    class RecorderDown(RuntimeError):
        """Distinct from the cycle's own fault, so the two can be told apart."""

    def _fail_with_a_broken_recorder(self):
        with faults.inject(self.s, "verification"):
            with faults.failing(
                    self.s.resilience, "record_incident",
                    exception=self.RecorderDown("incident store unavailable")):
                self.runCycle()

    def test_the_cycles_own_failure_is_what_reaches_the_caller(self):
        """What broke the job matters more than what broke the notebook."""
        with self.assertRaises(faults.InjectedFault) as caught:
            self._fail_with_a_broken_recorder()
        self.assertIsInstance(caught.exception.__cause__, self.RecorderDown,
                              "the recorder's failure was lost entirely")

    def test_the_panel_refuses_to_report_health(self):
        with self.assertRaises(Exception):
            self._fail_with_a_broken_recorder()
        finding = self.s.diagnostics.incidents()
        self.assertEqual(
            finding.state, "FAILED",
            "a failure nobody could record left the panel reading "
            f"{finding.state}; an empty table that cannot be written must "
            "never render the same as one with nothing in it")

    def test_a_recorder_that_comes_back_clears_the_panel(self):
        """Otherwise one outage latches the dashboard red for good."""
        with self.assertRaises(Exception):
            self._fail_with_a_broken_recorder()
        self.assertEqual(self.s.diagnostics.incidents().state, "FAILED")
        # The store comes back and the next incident writes normally. Driving
        # a second cycle would not do it: the shop is already holding a failed
        # job, so the next cycle stops gracefully instead of raising.
        self.s.resilience.record_incident(
            component="harness", operation="business-cycle",
            error_class="InjectedFault", signature="the recorder is back",
            severity=res.S_DEGRADED, trigger="probe", symptoms="probe")
        self.assertEqual(self.s.diagnostics.incidents().state, "DEGRADED")


class PruningProcessedIntents(unittest.TestCase):
    """Retention that deletes only what the audit log already holds."""

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.dir = pathlib.Path(self.box.name)
        self.s = Solvent(":memory:")
        self.addCleanup(self.s.store.close)
        self.spool = self.dir / "spool"
        (self.spool / "applied").mkdir(parents=True)
        (self.spool / "refused").mkdir(parents=True)

    def _intent(self, name: str, *, folder: str = "applied", age_days: float = 0,
                recorded: bool = True) -> pathlib.Path:
        import os
        import time
        path = self.spool / folder / f"{name}.intent"
        path.write_text('{"verb": "noop"}', encoding="utf-8")
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
        if recorded:
            self.s.audit.record(
                event="owner.intent_processed", authority="policy",
                initiator="owner:control-centre", why="test",
                input_ref=name, decision="noop", result="APPLIED")
        return path

    def prune(self, **kwargs):
        return prune_processed_intents(self.s, spool=str(self.spool), **kwargs)

    def test_an_old_recorded_intent_is_removed(self):
        path = self._intent("old", age_days=PROCESSED_INTENT_RETENTION_DAYS + 1)
        result = self.prune()
        self.assertFalse(path.exists())
        self.assertEqual(result["removed"], 1)

    def test_a_recent_intent_is_kept(self):
        path = self._intent("recent", age_days=1)
        self.prune()
        self.assertTrue(path.exists(), "a recent intent was deleted")

    def test_an_unrecorded_intent_is_never_removed_however_old(self):
        """Age is the second condition. The record is the first."""
        path = self._intent("unrecorded", age_days=10_000, recorded=False)
        result = self.prune()
        self.assertTrue(
            path.exists(),
            "an intent with no audit record was deleted; a recording bug and "
            "a cleanup bug must not combine into data loss")
        self.assertEqual(result["removed"], 0)

    def test_a_pending_intent_outside_the_pruned_folders_is_untouched(self):
        pending = self.spool / "waiting.intent"
        pending.write_text('{"verb": "noop"}', encoding="utf-8")
        self.prune()
        self.assertTrue(pending.exists(), "an unprocessed intent was deleted")

    def test_a_symlink_is_never_followed(self):
        outside = self.dir / "precious.txt"
        outside.write_text("not yours to delete", encoding="utf-8")
        link = self.spool / "applied" / "evil.intent"
        try:
            link.symlink_to(outside)
        except OSError:  # pragma: no cover - platform without symlinks
            self.skipTest("this platform does not support symlinks")
        self.s.audit.record(
            event="owner.intent_processed", authority="policy",
            initiator="owner:control-centre", why="test", input_ref="evil",
            decision="noop", result="APPLIED")
        import os
        import time
        when = time.time() - (PROCESSED_INTENT_RETENTION_DAYS + 1) * 86400
        if os.utime in os.supports_follow_symlinks:
            # Age the link itself: the pruner sees an old entry whose
            # target is young, which is the sharper version of the test.
            os.utime(link, (when, when), follow_symlinks=False)
        else:
            # Windows. ``os.utime`` cannot address a link without
            # following it -- ``NotImplementedError: utime:
            # follow_symlinks unavailable on this platform`` -- and the
            # old code hit that and failed the test rather than
            # skipping, because its only guard was around
            # ``symlink_to``.
            #
            # Ageing the target instead still makes the pruner want to
            # remove something older than the retention window through
            # this link, which is the behaviour being tested. What it
            # does not distinguish is an implementation that stats the
            # link versus one that stats the target -- so the assertion
            # that the target survives is doing the real work on both
            # platforms, and on Windows it is doing all of it.
            os.utime(outside, (when, when))
        self.prune()
        self.assertTrue(outside.exists(),
                        "pruning followed a symlink out of the spool")
        self.assertEqual(outside.read_text(encoding="utf-8"),
                         "not yours to delete",
                         "pruning wrote through a symlink out of the spool")

    def test_a_missing_spool_is_not_an_error(self):
        result = prune_processed_intents(self.s, spool=str(self.dir / "nope"))
        self.assertEqual(result["removed"], 0)

    def test_pruning_is_idempotent(self):
        self._intent("old", age_days=PROCESSED_INTENT_RETENTION_DAYS + 1)
        first = self.prune()
        second = self.prune()
        self.assertEqual(first["removed"], 1)
        self.assertEqual(second["removed"], 0)


class DiskProtectionUsesAbsoluteSpace(unittest.TestCase):
    """Percentage alone cannot see a nearly-full small disk."""

    def setUp(self):
        self.s = Solvent(":memory:")
        self.addCleanup(self.s.store.close)

    def test_a_disk_with_little_free_space_is_not_healthy(self):
        import collections

        from solvent import diagnostics as diag
        usage = collections.namedtuple("usage", "total used free")
        # A roomy disk by percentage -- 50% used -- but only 1 GB left,
        # which is the shape a small VPS actually fails in.
        tight = usage(total=2 * 1024 ** 3, used=1024 ** 3, free=1024 ** 3)
        original = diag.shutil.disk_usage
        diag.shutil.disk_usage = lambda _path: tight
        try:
            finding = self.s.diagnostics.disk()
        finally:
            diag.shutil.disk_usage = original
        self.assertNotEqual(
            finding.state, "HEALTHY",
            f"1 GB free read as {finding.state}; percentage alone is not "
            "enough to protect a disk MetaTrader 5 shares")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
