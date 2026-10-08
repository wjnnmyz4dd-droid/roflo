"""``python -m deskpilot_installer`` — the package's own entry point.

Exists so the engine can be launched as a module wherever its parent directory
is on ``sys.path``: the installed copy under ``C:\\DeskPilot\\Engine``, and the
tests. The wizard uses the bootstrap script instead, because under ``python
-I`` neither the current directory nor the script's own directory is on
``sys.path`` and there is nothing for ``-m`` to find.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
