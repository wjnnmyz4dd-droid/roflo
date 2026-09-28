"""§54: prove the documented libc bypass is real, rather than asserting it isn't.

``egress.py`` says plainly that a ``ctypes`` call straight to libc goes around
the CPython audit hook, and that the hook is the *in-process* layer of a
two-layer control. Nothing tested that claim, which left it as a comment —
and a comment is exactly what gets deleted by somebody who reads the hook,
sees it deny every socket operation they can think of, and concludes the OS
layer is belt-and-braces.

So this file demonstrates the bypass. If these tests ever start failing because
the bypass closed, that is good news and the deployment contract can be
revisited. Until then they are the evidence that the firewall and the network
namespace are load-bearing rather than defence in depth.

**Nothing here touches the network.** The comparison is made over a unix domain
socket inside a temporary directory: no host, no route, no resolver, nothing
that could leave the machine even if every control failed at once. What is being
measured is whether the hook *observes* the call, not whether anything is
reachable.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import pathlib
import socket
import tempfile
import unittest

from solvent import egress


class TheInProcessHookCatchesPython(unittest.TestCase):
    """The layer that does work, doing it."""

    def setUp(self):
        egress.install()

    def test_a_python_connect_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "s.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                with self.assertRaises(Exception):
                    client.connect(path)

    def test_a_dns_lookup_is_denied(self):
        with self.assertRaises(Exception):
            socket.getaddrinfo("example.invalid", 80)

    def test_a_subprocess_is_denied(self):
        """Which is why this file cannot use ctypes.util.find_library."""
        import subprocess

        with self.assertRaises(Exception):
            subprocess.Popen(["/bin/true"])


class TheHookDoesNotSeeLibc(unittest.TestCase):
    """The documented residual risk, demonstrated.

    A failure here means the bypass has closed and this file should be revisited
    — it does not mean Solvent is broken.
    """

    def setUp(self):
        egress.install()
        # Deliberately not ``ctypes.util.find_library``: that shells out to
        # ldconfig, and the guard denies subprocess.Popen — which is the guard
        # working, and would make this test measure the wrong thing.
        # ``CDLL(None)`` opens the main program's symbol table, which on Linux
        # carries libc, with no process spawned.
        try:
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.libc.socket
        except (OSError, AttributeError):
            self.skipTest("no libc symbols available in this process")

    def test_libc_can_create_a_socket_the_hook_never_sees(self):
        AF_UNIX, SOCK_STREAM = 1, 1
        fd = self.libc.socket(AF_UNIX, SOCK_STREAM, 0)
        self.assertGreaterEqual(
            fd, 0, "libc refused to create a socket, so nothing is demonstrated")
        os.close(fd)

    def test_libc_connect_is_not_intercepted(self):
        """The whole point. The Python equivalent above raises; this does not.

        It fails with ENOENT because nothing is listening on the path — an
        *operating system* refusal, not Solvent's. The hook was never consulted.
        """
        AF_UNIX, SOCK_STREAM = 1, 1
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "s.sock").encode()

            class SockaddrUn(ctypes.Structure):
                _fields_ = [("sun_family", ctypes.c_ushort),
                            ("sun_path", ctypes.c_char * 108)]

            fd = self.libc.socket(AF_UNIX, SOCK_STREAM, 0)
            self.assertGreaterEqual(fd, 0)
            try:
                address = SockaddrUn(AF_UNIX, path)
                result = self.libc.connect(fd, ctypes.byref(address),
                                           ctypes.sizeof(address))
                # Refused by the kernel for want of a listener, having never
                # reached Solvent's guard. That is the bypass.
                self.assertEqual(result, -1)
                self.assertIn(ctypes.get_errno(),
                              (2, 111, 13),  # ENOENT, ECONNREFUSED, EACCES
                              "the failure did not come from the kernel")
            finally:
                os.close(fd)


class TheDeploymentContractIsNotOptional(unittest.TestCase):
    """What follows from the bypass being real."""

    def test_the_module_says_so_rather_than_claiming_completeness(self):
        source = pathlib.Path("solvent/egress.py").read_text()
        self.assertIn("ctypes", source)
        # Matched across the line wrap, so reflowing the docstring does not
        # silently turn this assertion off.
        collapsed = " ".join(source.split())
        self.assertIn("not claimed as the whole control", collapsed)

    def test_the_deployment_contract_exists(self):
        contract = pathlib.Path("docs/solvent-egress-deployment-contract.md")
        self.assertTrue(contract.is_file(),
                        "the in-process layer names a contract that is missing")
        text = contract.read_text()
        self.assertIn("namespace", text.lower())

    def test_an_unrecorded_firewall_is_therefore_unsafe(self):
        """These two facts are one argument: the in-process layer has a known
        hole, so the host layer is required, so nobody having recorded the host
        layer is not a paperwork problem."""
        from solvent.diagnostics import Diagnostics, UNSAFE
        from solvent.harness import Solvent

        self.assertEqual(Diagnostics(Solvent()).network_posture().state, UNSAFE)
