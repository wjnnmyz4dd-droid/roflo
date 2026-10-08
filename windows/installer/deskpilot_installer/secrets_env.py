"""The owner's secrets: generated here, stored once, never shown.

Three secrets exist by the end of an installation. The signing key, which the
installer generates. The control-centre password hash, which the installer
derives from a password the owner typed. And, if the owner chose a cloud AI
backend, an API key they pasted. None of the three is ever displayed, logged,
passed as a command-line argument, or written anywhere but one file.

**The key is generated, not invented.** ``SOLVENT_OWNER_KEY`` authenticates
every consequential approval in DeskPilot, so a passphrase someone chose is a
different and much weaker scheme than 32 random bytes that satisfies the same
environment variable. Solvent's own :func:`solvent.owner.key_problem` already
refuses short, repeated and placeholder keys; :func:`generate_owner_key` is
built to pass that check by construction rather than by luck, and
:func:`self_check` re-derives the same rules so a regression in either place
shows up as a disagreement.

**The password hash comes from Solvent, not from here.** ``hash_password`` in
``solvent.web.auth`` is the authority for how a control-centre password is
stored -- PBKDF2, 600,000 iterations, one encoded form. Reimplementing it with
the same parameters would create a second authority that silently diverges the
day one of them is tuned. So :func:`hash_web_password` runs the installed
interpreter against the installed application and asks it, passing the password
on **stdin**: an argument would be visible in the process list to every other
user on that VPS, which is the reason Solvent's own command prompts for it.

**The file is locked down before it holds anything.** :func:`write_env_file`
creates the file, restricts it to SYSTEM and Administrators with ``icacls``,
and only then writes the secrets. The other order leaves a window in which the
owner's signing key sits on disk readable by every account on the machine.
"""

from __future__ import annotations

import secrets as _secrets
from dataclasses import dataclass

from .host import Host
from .report import Secret

#: Bytes of entropy in a generated owner key. Solvent's floor is 32 bytes of
#: *encoded* key; hex doubles the length, so this clears it with room to spare.
KEY_ENTROPY_BYTES = 32

#: Mirrors ``solvent.owner.MIN_KEY_BYTES``. Asserted equal by a test rather
#: than imported, because the installer must not depend on the application
#: being importable at the moment it generates a key.
MIN_KEY_BYTES = 32

OWNER_KEY_ENV = "SOLVENT_OWNER_KEY"
WEB_PASSWORD_HASH_ENV = "SOLVENT_WEB_PASSWORD_HASH"

#: The shortest password ``solvent.web.auth.hash_password`` will accept.
MIN_PASSWORD_LENGTH = 12


def generate_owner_key() -> Secret:
    """A fresh signing key, wrapped so it cannot be printed by accident."""
    return Secret(_secrets.token_hex(KEY_ENTROPY_BYTES), OWNER_KEY_ENV)


def self_check(key: Secret) -> str:
    """Why this key would be refused by Solvent, or ``""``.

    A deliberate second implementation of the rules in
    :func:`solvent.owner.key_problem`. The installer runs it before writing the
    key, so a key that the application would reject is caught at the moment it
    is created rather than the first time the owner tries to approve something.
    """
    raw = key.reveal().encode("utf-8")
    if not raw:
        return "the key is empty"
    if len(raw) < MIN_KEY_BYTES:
        return f"the key is {len(raw)} bytes; {MIN_KEY_BYTES} is the minimum"
    if len(set(raw)) < 4:
        return "the key has almost no variety in it"
    for width in range(1, 9):
        if len(raw) > width and raw == raw[:width] * (len(raw) // width) + \
                raw[:len(raw) % width]:
            return "the key is a short value repeated to reach the length limit"
    return ""


@dataclass(frozen=True, slots=True)
class EnvContents:
    """What goes into the environment file. Values are all :class:`Secret`."""

    owner_key: Secret
    web_password_hash: Secret | None = None
    extra: tuple[tuple[str, Secret], ...] = ()

    def lines(self) -> list[str]:
        """``KEY=value`` lines. The only place a secret is rendered."""
        out = [f"{OWNER_KEY_ENV}={self.owner_key.reveal()}"]
        if self.web_password_hash is not None:
            out.append(f"{WEB_PASSWORD_HASH_ENV}="
                       f"{self.web_password_hash.reveal()}")
        out += [f"{name}={value.reveal()}" for name, value in self.extra]
        return out


#: Header written above the secrets, for whoever opens the file later.
_HEADER = (
    "# DeskPilot secrets. Created by the DeskPilot installer.\n"
    "#\n"
    "# This file holds the owner signing key and the control-centre password\n"
    "# hash. It is deliberately excluded from DeskPilot's backups: a backup\n"
    "# that quietly contained these would turn every copy into somewhere they\n"
    "# can leak from. Back it up separately and deliberately, if at all.\n"
    "#\n"
    "# Anyone who can read this file can approve spending as the owner.\n"
    "# Access is restricted to SYSTEM and Administrators.\n"
)


def write_env_file(host: Host, path: str, contents: EnvContents) -> list[str]:
    """Create, lock down, then fill the environment file. Returns what was run.

    The ordering is the point. ``icacls`` is applied to an empty file so that
    no secret is ever on disk under inherited permissions, however briefly.
    """
    ran: list[str] = []
    host.write_bytes(path, _HEADER.encode("utf-8"))
    for argv in _lockdown_commands(path):
        result = host.run(list(argv), timeout=60.0)
        ran.append(" ".join(argv))
        if not result.ok:
            raise PermissionsRefused(
                f"could not restrict access to {path}: "
                f"{result.output.strip()[:200]}")
    body = _HEADER + "\n".join(contents.lines()) + "\n"
    host.write_bytes(path, body.encode("utf-8"))
    return ran


def _lockdown_commands(path: str) -> tuple[tuple[str, ...], ...]:
    """``icacls`` calls that leave only SYSTEM and Administrators.

    ``/inheritance:r`` first, because granting rights before removing
    inheritance leaves the inherited Users entry in place and the file readable
    by every account on the machine.
    """
    return (
        ("icacls.exe", path, "/inheritance:r"),
        ("icacls.exe", path, "/grant:r", "SYSTEM:(R,W)"),
        ("icacls.exe", path, "/grant:r", "Administrators:(R,W)"),
    )


class PermissionsRefused(Exception):
    """Raised when the secrets file could not be made private."""


def hash_web_password(host: Host, python_exe: str, app_dir: str,
                      password: Secret) -> Secret:
    """Ask the installed Solvent to hash the password. Never echoes it.

    The password travels on stdin. ``-I`` isolates the interpreter so that a
    stray ``PYTHONPATH`` -- or a planted module in the working directory --
    cannot substitute a ``hash_password`` that returns something useful to an
    attacker.
    """
    if len(password.reveal()) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"a control-centre password shorter than {MIN_PASSWORD_LENGTH} "
            f"characters is not one")
    script = (
        "import sys\n"
        "from solvent.web.auth import hash_password\n"
        "sys.stdout.write(hash_password(sys.stdin.read().rstrip('\\n')))\n"
    )
    # No ``PYTHONPATH``: ``-I`` ignores it, along with the current directory
    # and user site-packages. Passing it implied a mechanism that was not
    # operative. ``solvent`` is importable because the installer put a ``.pth``
    # naming the application directory into this interpreter's own
    # environment, which site processing honours even in isolated mode.
    result = host.run([python_exe, "-I", "-c", script], stdin=password,
                      cwd=app_dir, timeout=120.0)
    if not result.ok or not result.stdout.strip():
        raise ValueError(f"the password could not be hashed: "
                         f"{result.output.strip()[:200]}")
    encoded = result.stdout.strip()
    if not encoded.startswith("pbkdf2$"):
        raise ValueError("the hashing command returned something unexpected")
    return Secret(encoded, WEB_PASSWORD_HASH_ENV)
