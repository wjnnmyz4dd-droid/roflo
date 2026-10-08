#!/usr/bin/env python3
"""Launch the DeskPilot installation engine. The wizard runs *this* file.

It exists because of a specific failure. The wizard used to run
``python -I deskpilot_installer\\cli.py``, which cannot work: ``cli.py`` is a
module inside a package and uses relative imports, so executed as a script it
has no parent package and raises

    ImportError: attempted relative import with no known parent package

on its first import line. The obvious repair -- ``python -I -m
deskpilot_installer.cli`` -- does not work either, because ``-I`` is isolated
mode and in isolated mode ``sys.path`` contains neither the script's directory,
nor the current working directory, nor anything from ``PYTHONPATH``. There is
nothing for ``-m`` to find and nothing an environment variable can add.

So this file sits *beside* the package rather than inside it, is a plain script
with no relative imports of its own, and puts exactly one directory -- its own
-- on ``sys.path`` before importing the engine. Isolation is kept: no
``PYTHONPATH``, no user site-packages, no current directory. A planted
``json.py`` in whatever directory the installer was launched from still cannot
be imported, which is the property ``-I`` was chosen for.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from deskpilot_installer.cli import main as engine_main
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        # The owner must not be shown a bare traceback, and the wizard must be
        # able to stop before touching the machine. Both need this to say what
        # went wrong in one line and exit non-zero.
        sys.stderr.write(
            "DESKPILOT ENGINE COULD NOT START\n"
            f"  Why: {type(exc).__name__}: {exc}\n"
            f"  Engine directory: {here}\n"
            "  What was changed: nothing. The installation has not begun.\n"
            "  Safe next action: the installer package is incomplete or "
            "damaged. Obtain the installer again and run it without "
            "extracting it by hand.\n")
        return 3
    return engine_main()


if __name__ == "__main__":
    sys.exit(main())
