"""Process-level egress denial — the enforceable half of blocker B1.

The Action Gate is only a real control if code cannot simply go around it. A
dependency — an SDK, a document parser resolving a remote entity, a webhook
client — can open its own socket without ever consulting application logic. A
Gate that such a library can bypass is worse than no Gate, because it produces
false confidence.

This module installs a CPython audit hook (PEP 578) that denies network syscalls
and process spawning for the whole interpreter. Two properties matter:

* **It cannot be removed.** ``sys.addaudithook`` has no removal API, and adding a
  second hook does not displace the first. Once installed, it is installed.
* **It fires below ordinary application code.** ``socket.connect`` raises its audit
  event from CPython's C implementation, so ``httpx``, ``requests``, ``urllib``
  and anything else built on the socket module are all caught by one rule.

The only way through is :func:`window`, which the Action Gate opens around a
single approved call and closes immediately.

**Known residual risks, stated rather than hidden.** A ``ctypes`` call straight to
libc bypasses the hook, and code running in the same thread while a window is
open could piggyback on it. Neither is closed by anything in this process; both
are closed by the deployment contract in
``docs/solvent-egress-deployment-contract.md``, which puts workers in a network
namespace with no route and no resolver, and keeps credentials outside them. This
module is the *in-process* layer of a two-layer control, and is not claimed as
the whole control.
"""

from __future__ import annotations

import platform
import sys
import threading
from contextlib import contextmanager
from typing import Iterator

from .errors import EgressDenied

#: Audit events that represent leaving the process. ``subprocess`` and
#: ``os.system`` are included because a child process would escape an in-process
#: hook entirely — blocking the spawn is the only way to keep the boundary whole.
GUARDED_EVENTS = frozenset({
    "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.sendto", "socket.bind",
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
})

_state = threading.local()
_installed = False
_lock = threading.Lock()


def _window_open() -> bool:
    return getattr(_state, "open", False)


def _hook(event: str, args: tuple) -> None:
    if event not in GUARDED_EVENTS:
        return
    if _window_open():
        return
    raise EgressDenied(
        f"egress denied: {event} attempted outside an Action Gate window. "
        "All external effects must pass the Action Gate."
    )


def _warm_stdlib_caches() -> None:
    """Fill the standard-library caches that would otherwise need a child.

    ``platform.uname()`` on Windows under CPython 3.11 shells out to
    read the OS version, and caches the answer for the life of the
    process. Installing the guard before that first call therefore
    makes ``platform.node()``, ``platform.release()`` and
    ``platform.version()`` raise :class:`EgressDenied` forever after
    -- on Windows, on 3.11, and nowhere else, because 3.12 reads the
    registry instead.

    That is not theoretical. It took the Windows suite down on 3.11:
    a test installed the guard, five thousand tests later pytest's
    own ``--junitxml`` writer called ``platform.node()`` from
    ``pytest_sessionfinish``, the guard denied the spawn, and the
    session died *after* every test had passed and *before* the
    report was written. No summary, no exit code, nothing to certify.

    Warming is not a hole in the guard. The hook is installed
    immediately afterwards and denies every subsequent spawn; what
    changes is only that the standard library no longer needs one.
    The alternative -- exempting ``platform`` inside :func:`_hook` --
    would be a hole, because the exemption would be keyed on a
    stack frame and anything can arrange a stack frame.
    """
    try:
        platform.uname()
    except Exception:
        # A host where this fails is a host where the cache stays
        # cold. Nothing here is load-bearing enough to refuse to
        # install the guard over.
        pass


def install() -> None:
    """Install the guard. Idempotent, and irreversible for this interpreter."""
    global _installed
    with _lock:
        if _installed:
            return
        _warm_stdlib_caches()
        sys.addaudithook(_hook)
        _installed = True


def is_installed() -> bool:
    return _installed


@contextmanager
def window(reason: str) -> Iterator[None]:
    """Permit egress on this thread for one approved action.

    **Only the Action Gate may call this.** It is deliberately narrow: the window
    covers a single call and closes in a ``finally``, so an exception cannot leave
    it open.
    """
    if not reason:
        raise EgressDenied("an egress window requires a recorded reason")
    previous = getattr(_state, "open", False)
    _state.open = True
    try:
        yield
    finally:
        _state.open = previous


def probe() -> dict:
    """Report what the guard actually blocks here, for the readiness report.

    Used by tests and by ``solvent doctor`` so the owner sees measured behaviour
    rather than a claim in a document.
    """
    import socket

    results = {}
    try:
        # ``with`` matters: the probe usually ends in an exception, and a module
        # that polices the network must not leak a descriptor every time it runs.
        with socket.socket() as sock:
            sock.settimeout(1)
            sock.connect(("192.0.2.1", 443))  # TEST-NET-1, never routable
            results["socket.connect"] = "ALLOWED"
    except EgressDenied:
        results["socket.connect"] = "DENIED_BY_GUARD"
    except OSError as exc:
        results["socket.connect"] = f"OS_LEVEL: {exc.__class__.__name__}"
    try:
        socket.getaddrinfo("example.invalid", 80)
        results["dns"] = "ALLOWED"
    except EgressDenied:
        results["dns"] = "DENIED_BY_GUARD"
    except OSError as exc:
        results["dns"] = f"OS_LEVEL: {exc.__class__.__name__}"
    results["guard_installed"] = _installed
    return results
