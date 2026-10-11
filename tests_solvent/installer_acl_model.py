"""A model of the Windows access checks the isolation probe performs.

**This is not Windows.** It is a deliberately small reimplementation of the
parts of NTFS access control the installer relies on, built so that the
isolation step can be exercised end to end on a machine that is not Windows.
What a passing test here establishes is that *the rules the installer applies
would produce the required refusals under this model*. It does not establish
that Windows agrees. The difference is recorded in the report and in
``docs/deskpilot-windows-isolation.md``, and the installer's own probe -- which
runs on the owner's machine, as the real identity -- is what settles it.

It exists because the alternative was worse. A FakeHost that simply answered
"denied" to whatever the probe asked would make the isolation step pass with no
rules applied at all, which is the same class of mistake as the FakeHost that
once answered ``schtasks /Query`` with success for a task that had never been
registered. A model that can be wrong in the same places Windows can is more
use than a mock that cannot be wrong anywhere.

Modelled: explicit ACEs, ``/inheritance:r``, object inheritance (``OI``),
container inheritance (``CI``), inherit-only (``IO``), the simple rights
``F``/``M``/``RX``/``R``/``W``, the specific rights the installer uses, and the
OWNER RIGHTS substitution that decides whether a process can read back a file
it created.

Not modelled: privilege overrides such as SeBackupPrivilege, share
permissions, auditing, ACE order and deny entries (the installer uses none),
and mandatory integrity levels.
"""

from __future__ import annotations

import ntpath

#: Capabilities this model reasons about, named after the operation rather than
#: after the Windows bit, because the operation is what the probe tries.
READ = "read"          # open for reading: RD on a file
WRITE = "write"        # open for writing: WD on a file
LIST = "list"          # enumerate a directory: RD on a directory
ADD_FILE = "add"       # create a file in a directory: WD on a directory
DELETE = "delete"      # delete this object: D on it
DELETE_CHILD = "delete_child"
TRAVERSE = "traverse"
READ_ATTRS = "read_attrs"
EXECUTE = "execute"

#: ``icacls`` simple rights, expanded.
SIMPLE = {
    "F": {READ, WRITE, LIST, ADD_FILE, DELETE, DELETE_CHILD, TRAVERSE,
          READ_ATTRS, EXECUTE},
    "M": {READ, WRITE, LIST, ADD_FILE, DELETE, TRAVERSE, READ_ATTRS, EXECUTE},
    "RX": {READ, LIST, TRAVERSE, READ_ATTRS, EXECUTE},
    "R": {READ, LIST, READ_ATTRS},
    "W": {WRITE, ADD_FILE, READ_ATTRS},
}

#: ``icacls`` specific rights, as far as the installer uses them.
SPECIFIC = {
    "RD": {READ, LIST},     # read data on a file; list on a directory
    "WD": {WRITE, ADD_FILE},
    "AD": {ADD_FILE},
    "X": {TRAVERSE, EXECUTE},
    "RA": {READ_ATTRS},
    "DC": {DELETE_CHILD},
    "D": {DELETE},
    "WA": set(), "REA": {READ}, "WEA": set(), "RC": set(), "S": set(),
}

OWNER_RIGHTS_SID = "S-1-3-4"
ADMINS = "S-1-5-32-544"

#: ``BUILTIN\Users``, ``Everyone``, ``NT AUTHORITY\Authenticated Users``.
USERS_SID = "S-1-5-32-545"
EVERYONE_SID = "S-1-1-0"
AUTHENTICATED_USERS_SID = "S-1-5-11"

#: Group memberships that matter here.
#:
#: LOCAL SERVICE's token carries ``BUILTIN\Users``, and that is not a detail:
#: the default DACL on a directory created at the root of ``C:`` grants Users
#: read and execute by inheritance. So "the installer did not apply a rule"
#: does **not** mean "the control centre has no access" -- it means the control
#: centre keeps whatever the parent handed down.
#:
#: Modelling this is what makes a negative control worth running. Without it
#: the model denied everything it had not been told to allow, so removing a
#: protection made the result *more* restrictive and every "would the probe
#: notice?" test passed for the wrong reason.
GROUPS: dict[str, set[str]] = {
    "S-1-5-19": {USERS_SID, EVERYONE_SID, AUTHENTICATED_USERS_SID},   # LOCAL SERVICE
    "S-1-5-18": {EVERYONE_SID, AUTHENTICATED_USERS_SID},              # SYSTEM
}


def _norm(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path))


def _parse_rights(blob: str) -> set[str]:
    """``(OI)(CI)(F)`` or ``(WD,AD,X,RA)`` -> capabilities, ignoring flags."""
    out: set[str] = set()
    for group in blob.strip().strip("()").split(")("):
        for token in group.split(","):
            token = token.strip().upper()
            if token in ("OI", "CI", "IO", "NP", "I"):
                continue
            if token in SIMPLE:
                out |= SIMPLE[token]
            elif token in SPECIFIC:
                out |= SPECIFIC[token]
    return out


def _flags(blob: str) -> set[str]:
    return {g for g in blob.replace("(", " ").replace(")", " ").split()
            if g.upper() in ("OI", "CI", "IO")}


class Ace:
    """One granted entry: a SID, its capabilities, and how it inherits."""

    __slots__ = ("sid", "rights", "object_inherit", "container_inherit",
                 "inherit_only")

    def __init__(self, sid: str, spec: str) -> None:
        self.sid = sid.lstrip("*").upper()
        self.rights = _parse_rights(spec)
        flags = {f.upper() for f in _flags(spec)}
        self.object_inherit = "OI" in flags
        self.container_inherit = "CI" in flags
        self.inherit_only = "IO" in flags


class AclModel:
    """The DACLs ``icacls`` has been asked to set, and what they imply."""

    def __init__(self) -> None:
        #: path -> list of explicit ACEs
        self.explicit: dict[str, list[Ace]] = {}
        #: paths whose inheritance has been cut
        self.protected: set[str] = set()

    def seed_default_root(self, root: str) -> None:
        """The DACL a directory created at the root of ``C:`` starts with.

        Administrators and SYSTEM full control, and **Users read and execute**,
        all inheritable. This is the state the installer inherits and has to
        cut away, and seeding it is what lets a test tell "the rule was
        applied" from "the rule was not applied": without it, an unprotected
        path looks locked down because nothing had granted anything.
        """
        self.icacls(["icacls.exe", root,
                     "/grant", f"*{ADMINS}:(OI)(CI)(F)",
                     "/grant", "*S-1-5-18:(OI)(CI)(F)",
                     "/grant", f"*{USERS_SID}:(OI)(CI)(RX)"])

    # -- building -------------------------------------------------------
    def icacls(self, argv: list[str]) -> bool:
        """Apply one ``icacls`` command. False if it is not one we model."""
        if not argv or ntpath.basename(argv[0]).lower() != "icacls.exe":
            return False
        if len(argv) < 2:
            return False
        path = _norm(argv[1])
        rest = argv[2:]
        index = 0
        while index < len(rest):
            token = rest[index]
            low = token.lower()
            if low == "/inheritance:r":
                self.protected.add(path)
                index += 1
            elif low in ("/grant:r", "/grant"):
                spec = rest[index + 1] if index + 1 < len(rest) else ""
                sid, _, rights = spec.partition(":")
                aces = self.explicit.setdefault(path, [])
                if low == "/grant:r":
                    sid_key = sid.lstrip("*").upper()
                    aces[:] = [a for a in aces if a.sid != sid_key]
                aces.append(Ace(sid, rights))
                index += 2
            else:
                index += 1
        return True

    # -- asking ---------------------------------------------------------
    def effective(self, path: str, sid: str, *, is_dir: bool,
                  created_by: str | None = None) -> set[str]:
        """What ``sid`` may do to ``path``.

        ``created_by`` names the identity that created the object, which
        matters because an owner holds implicit rights -- unless an OWNER
        RIGHTS ACE is inherited onto the object, which replaces them. That
        substitution is the whole mechanism behind "the control centre cannot
        read back the request it just submitted".
        """
        sid = sid.lstrip("*").upper()
        key = _norm(path)
        rights: set[str] = set()

        # The identity itself and every group its token carries. A DACL entry
        # for Users applies to LOCAL SERVICE, which is the whole reason
        # /inheritance:r has to come first.
        identities = {sid} | GROUPS.get(sid, set())

        for ace in self.explicit.get(key, []):
            if ace.inherit_only:
                continue
            if ace.sid in identities:
                rights |= ace.rights

        for member in identities:
            rights |= self._inherited(key, member, is_dir=is_dir)

        owner_aces = self._owner_rights_aces(key, is_dir=is_dir)
        if created_by is not None and created_by.lstrip("*").upper() == sid:
            if owner_aces is None:
                # No OWNER RIGHTS entry: the implicit rights of an owner.
                rights |= {READ, WRITE, READ_ATTRS, DELETE}
            else:
                rights |= owner_aces
        return rights

    def _inherited(self, key: str, sid: str, *, is_dir: bool) -> set[str]:
        """Rights reaching ``key`` from an ancestor's inheritable ACEs."""
        rights: set[str] = set()
        if key in self.protected:
            return rights
        parent = ntpath.dirname(key)
        while parent and parent != key:
            for ace in self.explicit.get(parent, []):
                if ace.sid != sid:
                    continue
                if is_dir and ace.container_inherit:
                    rights |= ace.rights
                if not is_dir and ace.object_inherit:
                    rights |= ace.rights
            if parent in self.protected:
                break
            key, parent = parent, ntpath.dirname(parent)
        return rights

    def _owner_rights_aces(self, key: str, *, is_dir: bool):
        """The OWNER RIGHTS capabilities that apply to ``key``, or None.

        ``None`` means no such entry, so the owner keeps implicit rights.
        """
        # Accumulated, not first-match. Allow entries for the same SID add up
        # in a real DACL, so a second, more generous OWNER RIGHTS grant widens
        # what the owner may do -- which is precisely the mistake worth being
        # able to detect. Returning the first entry hid it.
        found = False
        rights: set[str] = set()
        for ace in self.explicit.get(key, []):
            if ace.sid == OWNER_RIGHTS_SID and not ace.inherit_only:
                found, rights = True, rights | ace.rights
        parent = ntpath.dirname(key)
        while parent and parent != key:
            for ace in self.explicit.get(parent, []):
                if ace.sid != OWNER_RIGHTS_SID:
                    continue
                if (is_dir and ace.container_inherit) or \
                        (not is_dir and ace.object_inherit):
                    found, rights = True, rights | ace.rights
            key, parent = parent, ntpath.dirname(parent)
        return rights if found else None

    def may(self, path: str, sid: str, capability: str, *, is_dir: bool = False,
            created_by: str | None = None) -> bool:
        return capability in self.effective(path, sid, is_dir=is_dir,
                                            created_by=created_by)
