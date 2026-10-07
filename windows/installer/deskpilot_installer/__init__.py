"""DeskPilot's Windows installation engine.

An orchestrator over DeskPilot's existing authorities, not a new one. It
decides nothing that ``solvent`` already decides: capability promotion,
readiness, diagnostics and firewall posture are read from the application by
running its own command-line interface against the installed database.

The package is pure standard library, like the application it installs. That is
not a stylistic preference -- DeskPilot's whole dependency story is that it has
none, so an installer needing packages of its own would reintroduce the pip
step this project exists to remove.
"""

from __future__ import annotations

#: The installer's own version. Independent of the payload it carries.
__version__ = "1.0.0"
