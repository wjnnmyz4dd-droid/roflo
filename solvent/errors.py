"""Typed failures.

Every failure in Solvent is a *refusal with a reason*, never a silent fallback.
Each exception below is raised at the point where an authority declines, so the
audit trail records which authority refused and why.
"""

from __future__ import annotations


class SolventError(Exception):
    """Base for every Solvent failure."""


class FailClosed(SolventError):
    """Raised when consequential authority is uncertain.

    The single most important error in the system. If Solvent cannot establish
    whether an action is authorised, it does not act. Unknown is not permission.
    """


class AuthorityError(SolventError):
    """A component attempted to write state it does not own.

    This is a programming error and an architectural violation, not a runtime
    condition. It means the one-authority law was broken in code.
    """


class ImmutableRecord(SolventError):
    """An append-only record was modified or deleted.

    Backed by database triggers, so this surfaces even when the attempt bypasses
    the application layer.
    """


class PermissionDenied(SolventError):
    """The Policy Store refused the action class."""


class GovernorRefused(SolventError):
    """The Financial Governor refused the spend or commitment.

    No component may catch this and proceed anyway; the refusal is final.
    """


class EgressDenied(SolventError):
    """The Action Gate refused an external effect, or one was attempted around it."""
