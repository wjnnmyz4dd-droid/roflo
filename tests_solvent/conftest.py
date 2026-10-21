"""Close databases the tests left open.

Windows cannot delete a file something still has open, so a test that
builds a ``Solvent`` over a temporary database and lets the temporary
directory clean itself up fails there with WinError 32 -- while passing
on POSIX, where unlinking an open file is ordinary. Six tests failed
exactly this way the first time the suite ran on a real Windows runner,
and nothing on Linux could have predicted which.

This closes whatever is still open once each test has finished. It is
**not** `ignore_cleanup_errors`: masking the error would leave the
product's own handle discipline untested, and the thing that actually
needed fixing -- that `Solvent` had no way to let go of its database --
would still be missing. The fixture closes handles; it does not pretend
the deletion worked.
"""

from __future__ import annotations

import pytest

from solvent.store import open_stores


@pytest.fixture(autouse=True)
def _close_databases_after_each_test():
    """Close only what this test opened.

    Snapshotting first matters: a class that builds one Solvent in
    `setUpClass` and shares it across its tests had that database closed
    after the first one, and the rest failed with "Cannot operate on a
    closed database". Tidying up after a test must not reach into
    something the test did not create.
    """
    before = {id(store) for store in open_stores()}
    yield
    for store in open_stores():
        if id(store) in before:
            continue
        try:
            store.close()
        except Exception:
            # A test that deliberately broke its database must not turn
            # into a teardown error reported against the next test.
            pass
