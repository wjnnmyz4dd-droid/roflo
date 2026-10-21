"""Close databases before the directory holding them is deleted.

Windows cannot delete a file that something still has open. POSIX can,
so a test that builds a ``Solvent`` or a ``ReadModel`` over a database
in a ``TemporaryDirectory`` and lets the directory clean itself up
passes on Linux and fails on Windows with WinError 32. The suite's
first run on a real Windows runner failed exactly this way, in the
five-skill library tests and in the full suite, and nothing on Linux
could have predicted which.

339 tests leave a file-backed handle open at the end. Fixing each one
by hand would be 339 chances to miss one, and the next test written
would reintroduce it. So the release happens where the deletion
happens: ``TemporaryDirectory.cleanup`` closes any database under the
directory it is about to remove, and then removes it.

Three things this deliberately is not:

* It is **not** ``ignore_cleanup_errors``. Masking the failure would
  leave the real defect in place -- that ``Solvent`` and ``ReadModel``
  had no way to let go of their database -- and that defect is a
  product one, not a test one. Both can now be closed and both are
  context managers.
* It does **not** close handles a test did not open under that
  directory. An earlier version closed everything open at the end of
  each test, which closed a class-scoped database after the first test
  of its class and left the rest failing with "Cannot operate on a
  closed database".
* It does **not** suppress a deletion that still fails. If something
  other than a DeskPilot database is holding the directory, that is a
  finding and it is allowed to raise.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from solvent.store import open_stores
from solvent.web.readmodel import open_readmodels


def _release(directory: str) -> list[str]:
    """Close every database open underneath ``directory``."""
    root = os.path.abspath(directory)
    closed = []
    for handle in list(open_stores()) + list(open_readmodels()):
        path = getattr(handle, "path", "") or ""
        if path == ":memory:" or not path:
            continue
        if os.path.abspath(path).startswith(root + os.sep):
            try:
                handle.close()
                closed.append(path)
            except Exception:
                # A test that deliberately broke its database must not
                # turn into a teardown error against the next one.
                pass
    return closed


@pytest.fixture(autouse=True)
def _close_what_this_test_opened():
    """Close databases a test opened, so few are open at once.

    The directory-scoped release below handles the ordering problem;
    this bounds the count. Snapshotting first, for the same reason as
    below: an earlier version closed everything open at the end of
    each test, which closed a class-scoped database after the first
    test of its class and left the rest failing with "Cannot operate
    on a closed database".

    **This is tidiness, not the fix for the Windows failure.** It was
    written as a hypothesis -- that the Windows suite dies partway
    because open handles accumulate -- and the measurement refuted it:
    without this fixture, 148 tests leave 3 databases open and none of
    them file-backed, which is nowhere near exhausting anything. The
    directory release was already catching the file-backed ones.

    The Windows failure is still unexplained. What is known: the suite
    stops after exactly 3,477 bytes of progress output, about 60%
    through, on a complete line, with no traceback and no summary, and
    the byte count is identical whether the output is tee'd or
    redirected. A deterministic stop with no Python-level error is the
    interpreter dying rather than a test failing.
    """
    before = {id(h) for h in open_stores()} | {id(h) for h in open_readmodels()}
    yield
    for handle in list(open_stores()) + list(open_readmodels()):
        if id(handle) in before:
            continue
        try:
            handle.close()
        except Exception:
            pass


@pytest.fixture(autouse=True, scope="session")
def _close_databases_before_their_directory_goes():
    original = tempfile.TemporaryDirectory.cleanup

    def cleanup(self):
        _release(self.name)
        return original(self)

    tempfile.TemporaryDirectory.cleanup = cleanup
    try:
        yield
    finally:
        tempfile.TemporaryDirectory.cleanup = original
