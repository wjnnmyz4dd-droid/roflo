"""§44–§46, §90: is the control centre reachable by anyone it should not be?

A firewall the owner meant to enable protects nothing, so every check here reads
the configuration that will actually be used. The important case is the middle
one: a posture nobody has recorded must report as unsafe, because treating
silence as default-deny turns the whole check into decoration.
"""

from __future__ import annotations

import pathlib
import unittest

from solvent.errors import FailClosed
from solvent.harness import OWNER, Solvent
from solvent.web import exposure

REPO = pathlib.Path(__file__).resolve().parent.parent


class ExposureIsAssessedFromConfiguration(unittest.TestCase):
    def test_the_default_binding_is_safe_once_the_posture_is_recorded(self):
        findings = exposure.assess(host="127.0.0.1", port=8765,
                                   firewall_default_deny=True)
        self.assertEqual(exposure.unsafe(findings), [])

    def test_an_unrecorded_firewall_posture_is_unsafe(self):
        """Unknown is not safe. This is the check that stops the other checks
        from being decoration."""
        findings = exposure.assess(host="127.0.0.1", port=8765,
                                   firewall_default_deny=None)
        names = [f.name for f in exposure.unsafe(findings)]
        self.assertIn("inbound firewall", names)

    def test_a_wildcard_bind_is_unsafe(self):
        for host in ("0.0.0.0", "::", ""):
            with self.subTest(host=host):
                names = [f.name for f in exposure.unsafe(
                    exposure.assess(host=host, port=8765,
                                    firewall_default_deny=True))]
                self.assertIn("wildcard bind", names)

    def test_a_public_bind_is_unsafe_unless_a_proxy_is_declared(self):
        names = [f.name for f in exposure.unsafe(
            exposure.assess(host="203.0.113.10", port=8765,
                            firewall_default_deny=True))]
        self.assertIn("bind address", names)

    def test_a_declared_proxy_makes_a_non_loopback_bind_acceptable(self):
        """Guards the test above: an assessment that refuses every real
        deployment would just be turned off."""
        findings = exposure.assess(host="10.0.0.5", port=8765,
                                   behind_proxy=True, tls_terminated=True,
                                   firewall_default_deny=True)
        self.assertEqual(exposure.unsafe(findings), [])

    def test_binding_a_privileged_port_is_unsafe(self):
        for port in (80, 443):
            with self.subTest(port=port):
                names = [f.name for f in exposure.unsafe(
                    exposure.assess(host="127.0.0.1", port=port,
                                    firewall_default_deny=True))]
                self.assertIn("privileged port", names)

    def test_insecure_cookies_are_unsafe_off_loopback(self):
        names = [f.name for f in exposure.unsafe(
            exposure.assess(host="10.0.0.5", port=8765, behind_proxy=True,
                            tls_terminated=True, secure_cookies=False,
                            firewall_default_deny=True))]
        self.assertIn("secure cookies", names)

    def test_no_password_configured_is_unsafe(self):
        names = [f.name for f in exposure.unsafe(
            exposure.assess(host="127.0.0.1", port=8765,
                            firewall_default_deny=True,
                            password_configured=False))]
        self.assertIn("authentication configured", names)

    def test_every_unsafe_finding_says_what_to_do(self):
        """A finding with no remedy is a complaint."""
        findings = exposure.assess(host="0.0.0.0", port=80,
                                   secure_cookies=False,
                                   firewall_default_deny=False,
                                   password_configured=False)
        for finding in exposure.unsafe(findings):
            with self.subTest(finding=finding.name):
                self.assertTrue(finding.remedy.strip())


class ThePostureIsRecordedByTheOwnerAndReadByReadiness(unittest.TestCase):
    def setUp(self):
        self.s = Solvent()

    def test_nothing_recorded_reads_as_unknown_not_as_yes(self):
        self.assertIsNone(self.s.policy.network_posture()["default_deny"])

    def test_readiness_reports_an_unrecorded_posture_as_blocking(self):
        report = self.s.readiness()
        check = next(c for c in report.checks
                     if c.name == "host network posture")
        self.assertFalse(check.ready)
        self.assertIn("nobody has recorded", check.detail)

    def test_recording_it_clears_the_check(self):
        self.s.policy.record_network_posture(
            owner_identity=OWNER, default_deny=True, proxy_port=443,
            tls_terminated=True, note="nftables default deny")
        check = next(c for c in self.s.readiness().checks
                     if c.name == "host network posture")
        self.assertTrue(check.ready, check.detail)

    def test_recording_it_as_open_does_not_clear_the_check(self):
        self.s.policy.record_network_posture(
            owner_identity=OWNER, default_deny=False, note="not done yet")
        check = next(c for c in self.s.readiness().checks
                     if c.name == "host network posture")
        self.assertFalse(check.ready)
        self.assertIn("does not default to deny", check.detail)

    def test_only_the_owner_may_record_it(self):
        for identity in ("web", "gate", "execution", "client:x"):
            with self.subTest(identity=identity):
                with self.assertRaises(FailClosed):
                    self.s.policy.record_network_posture(
                        owner_identity=identity, default_deny=True)

    def test_it_survives_a_restart(self):
        import tempfile

        db = str(pathlib.Path(tempfile.mkdtemp()) / "solvent.db")
        first = Solvent(db)
        first.policy.record_network_posture(owner_identity=OWNER,
                                           default_deny=True, proxy_port=443)
        first.store.close()
        restarted = Solvent(db)
        try:
            self.assertTrue(restarted.policy.network_posture()["default_deny"])
        finally:
            restarted.store.close()

    def test_it_is_on_the_audit_record(self):
        self.s.policy.record_network_posture(owner_identity=OWNER,
                                            default_deny=True)
        events = [e for e in self.s.audit.events()
                  if e["event"] == "policy.network_posture_recorded"]
        self.assertTrue(events)
        self.assertTrue(self.s.audit.verify_chain()[0])


class TheDeploymentArtifactsSayWhatTheyDo(unittest.TestCase):
    """A unit file and a firewall script are documentation that runs. If they
    disagree with the design, the design is the thing that is wrong."""

    @classmethod
    def setUpClass(cls):
        cls.unit = (REPO / "deploy" / "solvent-web.service").read_text()
        cls.firewall = (REPO / "deploy" / "firewall.sh").read_text()
        cls.env = (REPO / "deploy" / "solvent-web.env.example").read_text()

    def test_the_web_service_runs_as_its_own_user(self):
        """What makes read-only real rather than a promise in the code."""
        self.assertIn("User=solvent-web", self.unit)
        self.assertNotIn("User=solvent\n", self.unit)

    def test_the_web_service_mounts_the_database_read_only(self):
        self.assertIn("ReadOnlyPaths=/var/lib/solvent/solvent.db", self.unit)

    def test_the_web_service_may_write_only_the_intent_spool(self):
        write_lines = [l for l in self.unit.splitlines()
                       if l.startswith("ReadWritePaths=")]
        self.assertEqual(write_lines, ["ReadWritePaths=/var/lib/solvent/owner-intents"])

    def test_the_web_service_is_denied_outbound_network(self):
        self.assertIn("IPAddressDeny=any", self.unit)

    def test_the_web_service_env_file_is_not_the_runtime_one(self):
        """The control centre must not be handed the owner key or a payment
        secret, so it is given a file that has neither."""
        self.assertIn("/etc/solvent/solvent-web.env", self.unit)
        self.assertNotIn("/etc/solvent/solvent.env", self.unit)

    def test_the_web_env_template_contains_no_owner_key_line(self):
        self.assertNotIn("SOLVENT_OWNER_KEY=", self.env)
        self.assertNotIn("SOLVENT_STRIPE_WEBHOOK_SECRET=", self.env)
        self.assertIn("SOLVENT_WEB_PASSWORD_HASH=", self.env)

    def test_the_web_env_template_carries_no_value(self):
        for line in self.env.splitlines():
            if line.startswith("SOLVENT_"):
                with self.subTest(line=line):
                    self.assertTrue(line.endswith("="))

    def test_the_service_binds_loopback(self):
        self.assertIn("--host 127.0.0.1", self.unit)

    def test_the_firewall_defaults_to_drop_on_every_chain(self):
        for chain in ("input", "forward", "output"):
            with self.subTest(chain=chain):
                self.assertIn(f"chain {chain} {{", self.firewall)
        self.assertEqual(self.firewall.count("policy drop;"), 3)

    def test_the_firewall_does_not_open_the_control_centre_port(self):
        self.assertNotIn("tcp dport 8765", self.firewall)

    def test_the_firewall_is_a_dry_run_unless_told_otherwise(self):
        self.assertIn("--apply", self.firewall)
        self.assertIn("DRY RUN", self.firewall)

    def test_the_firewall_script_says_what_to_do_on_a_host_without_nftables(self):
        """A script that dies with "command not found" on a ufw host has told
        the owner nothing."""
        self.assertIn("ufw or firewalld", self.firewall)
