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


class TheStandardLibraryStillWorksBehindTheGuard(unittest.TestCase):
    """The guard must not make `platform.node()` permanently raise.

    This is the defect that took the Windows suite down on Python
    3.11, and it is worth stating precisely because it looks like a
    test-harness problem and is not.

    ``platform.uname()`` on Windows under CPython 3.11 shells out to
    read the OS version, then caches the result for the life of the
    process. 3.12 reads the registry instead, so the whole thing is
    invisible on 3.12, 3.13 and on every Linux. On 3.11 Windows, a
    process that installs the guard before ever calling
    ``platform.uname()`` gets an interpreter where ``platform.node()``,
    ``platform.release()`` and ``platform.version()`` raise
    :class:`EgressDenied` for the rest of its life.

    What that cost: pytest's ``--junitxml`` writer calls
    ``platform.node()`` from ``pytest_sessionfinish``. The guard denied
    the spawn, the session died *after* all 5,414 tests had run and
    *before* the report was written, and the job ended with no
    summary, no junit.xml and nothing to certify. Every test had
    passed.

    ``install`` now fills that cache immediately before adding the
    hook. The hook is unchanged and still denies every spawn --
    which is what the second test here is for. Exempting ``platform``
    inside the hook would have been the hole: an exemption keyed on a
    stack frame is an exemption anything can arrange.
    """

    def test_platform_node_answers_with_the_guard_armed(self):
        """What the suite's own reporter needs, asserted on every host.

        This cannot reproduce the Windows failure on Linux -- there
        ``platform.uname`` reads ``os.uname`` and spawns nothing, so
        the guard was never in its way. What it can do, and does, is
        hold the outcome: with the guard armed, the three functions
        pytest's junit writer calls must answer. On 3.11 Windows that
        is true only because of the warming; everywhere else it is
        true anyway, and a test that passes for the easy reason on
        one host and the hard reason on another is still the test
        that fails when the warming is removed.
        """
        import platform
        egress.install()
        platform._uname_cache = None          # as if never asked
        egress._warm_stdlib_caches()
        self.assertTrue(egress.is_installed())
        self.assertIsNotNone(platform.node())
        self.assertIsNotNone(platform.release())
        self.assertIsNotNone(platform.version())

    def test_warming_the_cache_opens_no_window(self):
        """The guard denies exactly as much afterwards as before."""
        import subprocess
        egress.install()
        egress._warm_stdlib_caches()
        self.assertFalse(egress._window_open())
        with self.assertRaises(EgressDenied):
            subprocess.Popen(["/bin/true"])
        with self.assertRaises(EgressDenied):
            with socket.socket() as sock:
                sock.connect(("192.0.2.1", 443))

    def test_install_warms_before_it_arms(self):
        """Order is the whole fix; after is too late on 3.11 Windows."""
        import inspect
        source = inspect.getsource(egress.install)
        self.assertLess(source.index("_warm_stdlib_caches"),
                        source.index("addaudithook"),
                        "warming after the hook is installed warms nothing")

    def test_a_host_that_cannot_answer_does_not_block_installation(self):
        """A cold cache is survivable; refusing to arm the guard is not."""
        import platform
        original = platform.uname
        platform.uname = lambda: (_ for _ in ()).throw(OSError("no uname"))
        try:
            egress._warm_stdlib_caches()       # must not raise
        finally:
            platform.uname = original
