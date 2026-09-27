"""The owner's control surface over Solvent. A window, not a second Solvent.

**Why this is a separate process, and why that is not a deployment detail.**
Constructing a :class:`solvent.harness.Solvent` installs the egress audit hook,
which denies ``socket.bind`` for the whole interpreter and cannot be removed.
A web server must bind a port. So the two cannot share a process — and the
consequence is the security property this package is built around: the web
process does not hold Solvent's authorities, because it cannot.

That is the right shape anyway. Assume the website is compromised. Then:

* **It cannot perform an external effect.** It has no Action Gate. Nothing in
  this package can send, charge, or call out, and the egress hook is not even
  the reason — there is simply no code path.
* **It cannot rewrite what it displays.** Everything on screen comes from
  :mod:`solvent.web.readmodel`, which opens SQLite ``mode=ro``. A read-only
  connection cannot be talked into a write.
* **It cannot decide anything.** Owner actions are written to a spool as
  *intents*. The Solvent runtime reads them and executes them under its own
  authorities, which apply their own rules — so an intent asking for a
  capability promotion still meets a registry that demands an owner identity and
  evidence, and loses.
* **It does not hold the owner's signing key.** ``SOLVENT_OWNER_KEY`` is never
  read here, never sent to a browser, and never needed to display anything.

What is left for a compromised web process is: reading business data, and asking
for things. That is a real loss and it is stated plainly rather than designed
away — see ``docs/solvent-web-control-center.md``.
"""

from __future__ import annotations
