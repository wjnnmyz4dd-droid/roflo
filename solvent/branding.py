"""The product name, and the engine name, kept apart on purpose.

DeskPilot is what the owner bought and what the owner sees. Solvent is the
engine it runs on: the Python package, the database, the CLI, the service
unit, the audit records, the signed payloads. Both names are correct; they
name different things.

This module exists so that the distinction is *executable* rather than a
convention someone has to remember. Owner-facing text imports :data:`PRODUCT`.
Internal identifiers are spelled ``solvent`` in the source, because renaming
them would mean a migration, and a migration is not a branding decision.

**Do not run a repository-wide replacement of "Solvent".** The name appears in
places where it is a contract, not a label:

* the Python package ``solvent`` and every import of it
* the database file, its tables, and their migration identifiers
* the ``SOLVENT_OWNER_KEY`` environment variable
* the ``solvent_session`` cookie and the ``solvent_job_id`` payment metadata key
* ``/var/lib/solvent`` and ``/etc/solvent/solvent.env``
* the ``solvent`` CLI, which existing deployment instructions invoke
* ``solvent.service``
* audit event names and historical audit rows, which are immutable evidence
* signed approval payloads, whose fields are covered by a signature

Changing any of those breaks a running installation to no owner benefit.
"""

from __future__ import annotations

#: What the owner sees. One word, capitalised exactly like this.
PRODUCT = "DeskPilot"

#: What runs underneath. Shown only where the engine identity is useful —
#: diagnostics, the about page, technical evidence.
ENGINE = "Solvent"

#: For pages that want to be explicit about the relationship.
POWERED_BY = f"{PRODUCT} · engine: {ENGINE}"

#: One sentence, for the top of a page or a manual.
POSITION = f"{PRODUCT} is an AI office operating system."
