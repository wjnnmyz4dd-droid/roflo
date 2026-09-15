"""Egress bypass attempts. Every one of these must fail to reach the network.

These are the in-process half of blocker B1. The OS half — a worker namespace
with no route and no resolver — is verified by ``tools/verify_egress.py``, which
needs to spawn processes and so cannot run inside a suite that (correctly)
forbids spawning.
"""

import socket
import unittest
import urllib.request

from solvent import egress
from solvent.errors import EgressDenied


class GuardInstallation(unittest.TestCase):
    def test_guard_is_installed_and_idempotent(self):
        egress.install()
        egress.install()
        self.assertTrue(egress.is_installed())

    def test_guard_cannot_be_displaced_by_adding_another_hook(self):
        """PEP 578 hooks cannot be removed; a later hook does not override an earlier one."""
        import sys
        egress.install()
        sys.addaudithook(lambda event, args: None)
        with self.assertRaises(EgressDenied):
            with socket.socket() as sock:
                sock.connect(("192.0.2.1", 443))


class BypassAttempts(unittest.TestCase):
    """G-1 .. G-6 from the P0 contract, as executable attempts."""

    def setUp(self):
        egress.install()

    def test_g1_raw_socket_connect_is_denied(self):
        with self.assertRaises(EgressDenied):
            with socket.socket() as sock:
                sock.connect(("192.0.2.1", 443))

    def test_g2_dns_resolution_is_denied(self):
        with self.assertRaises(EgressDenied):
            socket.getaddrinfo("example.com", 443)

    def test_g3_a_library_making_its_own_request_is_denied(self):
        """The whole point: a dependency that never consults the Gate still fails."""
        with self.assertRaises(EgressDenied):
            urllib.request.urlopen("http://192.0.2.1/", timeout=1)

    def test_g4_subprocess_spawn_is_denied(self):
        """A child process would escape an in-process hook, so the spawn is blocked."""
        import subprocess
        with self.assertRaises(EgressDenied):
            subprocess.Popen(["/bin/true"])

    def test_os_system_is_denied(self):
        import os
        with self.assertRaises(EgressDenied):
            os.system("true")

    def test_socket_bind_is_denied(self):
        """Listening is an external effect too: it invites traffic in."""
        with self.assertRaises(EgressDenied):
            with socket.socket() as sock:
                sock.bind(("0.0.0.0", 0))


class GateWindow(unittest.TestCase):
    def setUp(self):
        egress.install()

    def test_window_permits_egress_then_closes(self):
        with egress.window("approved action"):
            self.assertTrue(egress._window_open())
        self.assertFalse(egress._window_open())
        with self.assertRaises(EgressDenied):
            with socket.socket() as sock:
                sock.connect(("192.0.2.1", 443))

    def test_window_closes_even_when_the_action_raises(self):
        """An exception must not leave the boundary open."""
        with self.assertRaises(ValueError):
            with egress.window("failing action"):
                raise ValueError("transport blew up")
        self.assertFalse(egress._window_open())
        with self.assertRaises(EgressDenied):
            with socket.socket() as sock:
                sock.connect(("192.0.2.1", 443))

    def test_window_requires_a_reason(self):
        with self.assertRaises(EgressDenied):
            with egress.window(""):
                pass

    def test_window_is_thread_local(self):
        """Opening a window on one thread must not open it for another."""
        import threading
        seen = {}

        def other():
            try:
                with socket.socket() as sock:
                    sock.connect(("192.0.2.1", 443))
                seen["result"] = "ALLOWED"
            except EgressDenied:
                seen["result"] = "DENIED"
            except OSError:
                seen["result"] = "OS_LEVEL"

        with egress.window("main thread action"):
            thread = threading.Thread(target=other)
            thread.start()
            thread.join()
        self.assertEqual(seen["result"], "DENIED")


class Probe(unittest.TestCase):
    def test_probe_reports_measured_behaviour(self):
        egress.install()
        report = egress.probe()
        self.assertTrue(report["guard_installed"])
        self.assertEqual(report["socket.connect"], "DENIED_BY_GUARD")
        self.assertEqual(report["dns"], "DENIED_BY_GUARD")


if __name__ == "__main__":
    unittest.main()
