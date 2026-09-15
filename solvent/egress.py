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


def install() -> None:
    """Install the guard. Idempotent, and irreversible for this interpreter."""
    global _installed
    with _lock:
        if _installed:
            return
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
