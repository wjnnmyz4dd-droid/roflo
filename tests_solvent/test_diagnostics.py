"""Does Solvent know when something is wrong, and does it say so honestly?

The tempting failure is not a missed check. It is a green light: an exception
swallowed into "probably fine", a control nobody confirmed reported as present,
a number nobody measured reported as good. Every test here attacks one of those.

And underneath: **diagnosis is not authority**. Noticing a problem does not
license fixing it, and the module that notices must not be able to act.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from solvent import diagnostics as diag
from solvent.harness import OWNER, Solvent


class UnknownIsNotHealthy(unittest.TestCase):
    """The rule the whole classification rests on."""

    def test_unknown_ranks_worse_than_healthy(self):
        self.assertGreater(diag._RANK[diag.UNKNOWN], diag._RANK[diag.HEALTHY])

    def test_a_check_that_cannot_run_reports_unknown_not_healthy(self):
        """An exception in a health check is where silence is most dangerous
        and most likely: the surrounding code is already in trouble."""
        solvent = Solvent()

        class Exploding:
            def verify_chain(self):
                raise RuntimeError("the database went away")

        solvent.audit = Exploding()
        finding = diag.Diagnostics(solvent).audit_chain()
        self.assertEqual(finding.state, diag.UNKNOWN)
        self.assertIn("could not run", finding.detail)

    def test_a_failing_check_does_not_vanish_from_the_report(self):
        """A missing finding reads exactly like a passing one."""
        solvent = Solvent()

        class Exploding:
            def verify_chain(self):
                raise RuntimeError("boom")

        solvent.audit = Exploding()
        report = diag.Diagnostics(solvent).run()
        names = [f.name for f in report.findings]
        self.assertIn("Audit chain", names)

    def test_a_secret_in_a_failing_check_is_redacted(self):
        import os

        canary = "canary-owner-key-never-real-0123456789abcdef"
        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = canary
        self.addCleanup(
            lambda: (os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
                     if previous is not None
                     else os.environ.pop("SOLVENT_OWNER_KEY", None)))
        solvent = Solvent()

        class Exploding:
            def verify_chain(self):
                raise RuntimeError(f"failed using {canary}")

        solvent.audit = Exploding()
        self.assertNotIn(canary, diag.Diagnostics(solvent).audit_chain().detail)

    def test_the_worst_state_drives_the_summary(self):
        report = diag.Report(findings=[
            diag.Finding("a", diag.HEALTHY, ""),
            diag.Finding("b", diag.UNKNOWN, ""),
            diag.Finding("c", diag.HEALTHY, "")])
        self.assertEqual(report.worst, diag.UNKNOWN)

    def test_an_empty_report_is_unknown_rather_than_healthy(self):
        self.assertEqual(diag.Report(findings=[]).worst, diag.UNKNOWN)


class UnrecordedIsNotSafe(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.d = diag.Diagnostics(self.s)

    def test_an_unrecorded_firewall_is_unsafe(self):
        """A script in the repository is not a firewall on the host."""
        self.assertIsNone(self.s.policy.network_posture().get("default_deny"))
        finding = self.d.network_posture()
        self.assertEqual(finding.state, diag.UNSAFE)
        self.assertTrue(finding.owner_action)

    def test_a_firewall_recorded_as_open_is_unsafe(self):
        self.s.policy.record_network_posture(
            owner_identity=OWNER, default_deny=False,
            note="the owner recorded that it is not applied")
        self.assertEqual(self.d.network_posture().state, diag.UNSAFE)

    def test_a_firewall_recorded_as_default_deny_is_healthy(self):
        self.s.policy.record_network_posture(
            owner_identity=OWNER, default_deny=True,
            note="applied and recorded")
        self.assertEqual(self.d.network_posture().state, diag.HEALTHY)

    def test_an_absent_backup_directory_is_unknown_not_healthy(self):
        finding = self.d.backups()
        self.assertEqual(finding.state, diag.UNKNOWN)
        self.assertIn("nothing can be said", finding.detail)

    def test_no_certified_verifier_is_unknown_not_healthy(self):
        self.assertEqual(self.d.verifiers().state, diag.UNKNOWN)


class ItReportsWhatIsActuallyTrue(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.d = diag.Diagnostics(self.s)

    def test_a_cold_start_cannot_deliver_and_says_so(self):
        finding = self.d.capability_registry()
        self.assertEqual(finding.state, diag.OWNER_ACTION_REQUIRED)
        self.assertEqual(finding.response, diag.BLOCK_WORKFLOW)
        self.assertIn("solvent setup capability", finding.owner_action)

    def test_a_provisioned_deployment_reports_what_it_can_deliver(self):
        from solvent.harness import CSV_PROMOTION, provision_capability

        provision_capability(self.s, CSV_PROMOTION, owner_identity=OWNER)
        finding = diag.Diagnostics(self.s).capability_registry()
        self.assertEqual(finding.state, diag.HEALTHY)
        self.assertIn("csv-cleanup", finding.detail)

    def test_simulated_revenue_is_not_reported_as_a_ledger_fault(self):
        self.assertEqual(self.d.ledger_integrity().state, diag.HEALTHY)

    def test_real_revenue_while_simulating_is_a_contradiction(self):
        """One of those two statements is wrong, and the owner needs to know
        which before trusting any total."""
        class Contradiction:
            def real_revenue_cents(self):
                return 12_000

        self.s.ledger = Contradiction()
        finding = diag.Diagnostics(self.s).ledger_integrity()
        self.assertEqual(finding.state, diag.FAILED)
        self.assertEqual(finding.response, diag.OWNER_ESCALATION)

    def test_an_open_incident_is_visible(self):
        self.s.resilience.record_incident(component="c", operation="o",
                                          error_class="E")
        self.assertNotEqual(self.d.incidents().state, diag.HEALTHY)

    def test_an_open_breaker_is_visible(self):
        from solvent import resilience as res

        for _ in range(res.DEFAULT_FAILURE_THRESHOLD):
            self.s.resilience.failure("model")
        finding = self.d.breakers()
        self.assertEqual(finding.state, diag.DEGRADED)
        self.assertIn("model", finding.detail)

    def test_a_broken_audit_chain_asks_for_a_halt(self):
        class Broken:
            def verify_chain(self):
                return False, "seq 4: content hash mismatch"

        self.s.audit = Broken()
        finding = diag.Diagnostics(self.s).audit_chain()
        self.assertEqual(finding.state, diag.FAILED)
        self.assertEqual(finding.response, diag.HALT_REQUIRED)

    def test_payment_ingress_says_it_is_not_needed_for_a_first_trial(self):
        """A first supervised job does not collect money, and reporting the
        rail as a blocker would send the owner to set up Stripe first."""
        finding = self.d.payment_ingress()
        self.assertIn("first supervised trial", finding.detail)
        self.assertEqual(finding.response, diag.OBSERVE)

    def test_every_finding_names_a_response(self):
        for finding in self.d.run().findings:
            with self.subTest(finding=finding.name):
                self.assertIn(finding.response, diag.RESPONSES)

    def test_every_finding_names_a_state(self):
        for finding in self.d.run().findings:
            with self.subTest(finding=finding.name):
                self.assertIn(finding.state, diag.STATES)

    def test_anything_needing_the_owner_says_what_to_do(self):
        for finding in self.d.run().findings:
            if finding.state == diag.OWNER_ACTION_REQUIRED:
                with self.subTest(finding=finding.name):
                    self.assertTrue(finding.owner_action,
                                    f"{finding.name} needs the owner and does "
                                    "not say what for")


class HostMetrics(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()
        self.d = diag.Diagnostics(self.s)

    def test_it_reports_the_numbers_an_operator_asks_for(self):
        host = self.d.host()
        for key in ("uptime_seconds", "disk_total_bytes", "disk_free_bytes",
                    "disk_used_percent", "database_bytes", "audit_events"):
            self.assertIn(key, host)

    def test_memory_is_absent_rather_than_invented_where_unavailable(self):
        """A made-up memory figure is worse than none: somebody would act on it."""
        memory = self.d.host()["memory"]
        self.assertIsInstance(memory, dict)
        if memory:
            self.assertIn("used_percent", memory)

    def test_uptime_is_measured_from_a_real_start(self):
        import time

        diagnostics = diag.Diagnostics(self.s, started_at=time.time() - 120)
        self.assertGreaterEqual(diagnostics.host()["uptime_seconds"], 119)

    def test_the_database_size_counts_the_write_ahead_log(self):
        """The main file is a 4 KB header under WAL, so counting it alone
        reports a database that is almost empty however much is in it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "solvent.db")
            solvent = Solvent(path)
            for index in range(50):
                solvent.audit.record(event="filler", authority="test",
                                     initiator="test", why=f"row {index}")
            host = diag.Diagnostics(solvent).host()
            self.assertGreater(host["database_bytes"], 0)
            self.assertGreaterEqual(host["database_bytes"], host["wal_bytes"])


class DiagnosisIsNotAuthority(unittest.TestCase):
    def test_running_diagnostics_changes_nothing(self):
        solvent = Solvent()
        before = {
            "policy": solvent.policy.version,
            "mode": solvent.policy.operating_mode.value,
            "capabilities": sorted(c.name for c in solvent.capability.capabilities()),
            "ledger": len(solvent.store.raw_readonly("SELECT * FROM ledger_entries")),
            "audit": len(solvent.audit.events()),
            "incidents": len(solvent.resilience.incidents()),
        }
        diag.Diagnostics(solvent).run()
        diag.Diagnostics(solvent).host()
        self.assertEqual({
            "policy": solvent.policy.version,
            "mode": solvent.policy.operating_mode.value,
            "capabilities": sorted(c.name for c in solvent.capability.capabilities()),
            "ledger": len(solvent.store.raw_readonly("SELECT * FROM ledger_entries")),
            "audit": len(solvent.audit.events()),
            "incidents": len(solvent.resilience.incidents()),
        }, before)

    def test_it_owns_exactly_one_table_and_that_table_is_its_own_runs(self):
        """It used to own none. It now owns the record of its own reports, so
        the control centre can render an authoritative finding instead of
        deciding for itself what the state means.

        "Owns no table" was a proxy for "commands nothing". The property that
        actually matters is asserted directly: diagnostics may write its own
        report and may write nothing else, which ``AuthorityConnection``
        enforces at the connection rather than by convention."""
        from solvent.store import TABLE_OWNER

        owned = {t for t, o in TABLE_OWNER.items() if o == "diagnostics"}
        self.assertEqual(owned, {"diagnostic_reports"})

    def test_it_cannot_write_to_another_authoritys_table(self):
        """The real invariant, exercised rather than asserted from a list."""
        from solvent.errors import AuthorityError

        db = Solvent().store.for_authority("diagnostics")
        for table in ("policy_current", "ledger_entries", "audit_log",
                      "registered_capabilities", "jobs", "action_requests"):
            with self.assertRaises(AuthorityError, msg=table):
                db.execute(f"UPDATE {table} SET ts = 'tampered'")

    def test_it_recommends_a_halt_and_cannot_perform_one(self):
        """The strongest response it can name, and it still cannot act on it."""
        source = pathlib.Path("solvent/diagnostics.py").read_text()
        self.assertIn("HALT_REQUIRED", source)
        for forbidden in ("set_operating_mode", "amend(", "register(",
                          "record_delivery", "issue("):
            self.assertNotIn(forbidden, source,
                             f"diagnostics calls {forbidden}; noticing a "
                             "problem is not licence to act on it")

    def test_its_only_write_is_its_own_report(self):
        """One INSERT, into one table. No UPDATE, DELETE or DROP anywhere: a
        report is a new row, never an edit to an old one, so the history of
        what the system looked like cannot be rewritten."""
        tree = ast.parse(pathlib.Path("solvent/diagnostics.py").read_text())
        writes = [n.value.strip() for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                  and n.value.strip().upper().startswith(
                      ("INSERT", "UPDATE", "DELETE", "DROP"))]
        self.assertEqual(len(writes), 1, writes)
        self.assertTrue(writes[0].upper().startswith(
            "INSERT INTO DIAGNOSTIC_REPORTS"), writes[0])
