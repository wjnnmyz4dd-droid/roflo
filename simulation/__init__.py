"""Isolated synthetic infrastructure for the business simulation.

Nothing in :mod:`solvent` imports this package and nothing here is a
production authority. It supplies three things the production system
deliberately does not have and should not have:

* a **fictional business profile**, so invoices can be issued without a
  real owner's legal identity (:mod:`simulation.profile`);
* a **fictional customer population** with persistent identity, budget,
  behaviour and history (:mod:`simulation.customers`);
* a **deterministic business calendar** that advances simulated days
  without waiting for real ones (:mod:`simulation.calendar`).

The division of authority is deliberate and is the whole reason this
package is separate:

===================  =====================================================
customers            decide how fictional clients behave
DeskPilot            decides what the business does about it
:mod:`simulation.audit`  grades the result, sharing no code with either
===================  =====================================================

Every identifier here is fictional. Addresses use ``.invalid``, which
is reserved by RFC 2606 and can never resolve, so nothing in this
package can reach a real person even by accident.
"""

from __future__ import annotations

__all__ = ["profile", "customers", "calendar", "audit"]
