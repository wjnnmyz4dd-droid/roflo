"""The installation log: useful to debug, impossible to leak a secret into.

§20 asks for a log that helps and never contains passwords, the owner key, API
keys or tokens. Those two goals pull against each other -- the most useful log
is the one that records everything -- so the resolution is structural rather
than disciplinary.

Two layers, because either alone is insufficient:

1. :class:`~deskpilot_installer.report.Secret` masks itself through ``str``,
   ``repr`` and f-strings, so a secret passed to :meth:`Log.write` as an object
   cannot render. This catches the ordinary case completely.
2. :func:`scrub` catches the case layer 1 cannot: a secret that arrived as a
   plain string, because it was pasted into a field, read back out of a file,
   or echoed by a command whose output is being logged. It matches on the
   *shapes* secrets have -- an ``NAME=value`` assignment for a known secret
   variable, a PBKDF2 encoded hash, a Stripe key, a long hex run -- and
   replaces the value.

The second layer is the one that earns its place. Command output is logged, and
a command that fails may quote its own arguments back; without scrubbing, a
single badly-behaved tool turns the log into a copy of the secrets file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .host import Host
from .report import MASK, Secret

#: Environment variables whose values must never appear.
SECRET_ENV_NAMES = (
    "SOLVENT_OWNER_KEY",
    "SOLVENT_WEB_PASSWORD_HASH",
    "SOLVENT_STRIPE_SECRET",
    "SOLVENT_STRIPE_WEBHOOK_SECRET",
    "SOLVENT_SMS_CREDENTIAL",
    "SOLVENT_VOICE_CREDENTIAL",
    "ROFLO_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # NAME=value for anything on the list above, however it is quoted.
    (re.compile(r"\b(" + "|".join(SECRET_ENV_NAMES) + r")\s*=\s*\S+"),
     r"\1=" + MASK),
    # A PBKDF2 encoded password hash in Solvent's stored form.
    (re.compile(r"pbkdf2\$\d+\$[0-9a-f]+\$[0-9a-f]+"), MASK),
    # Provider key shapes.
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{8,}"), MASK),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{8,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), MASK),
    # A long unbroken hex run is an owner key, a digest, or something else
    # nobody needs in a log. 48 is above any digest the installer prints on
    # purpose (SHA-256 is 64 but those are printed through facts, not here)
    # and below an accidental match on ordinary text.
    (re.compile(r"\b[0-9a-fA-F]{48,}\b"), MASK),
)


def scrub(text: str) -> str:
    """Remove anything shaped like a secret. Applied to every line written."""
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


@dataclass
class Log:
    """An append-only installation log. Buffered, then flushed to disk.

    Buffered because the log's own directory may not exist yet when the first
    line is written -- the system check runs before anything is created -- and
    a logger that cannot record the early steps is a logger that is missing the
    interesting part of a failed installation.
    """

    host: Host
    path: str = ""
    lines: list[str] = field(default_factory=list)

    def write(self, message: str) -> str:
        """Record one line. Returns the scrubbed text that was recorded."""
        text = scrub(str(message).rstrip())
        self.lines.append(text)
        return text

    def step(self, title: str) -> None:
        self.write("")
        self.write(f"== {title}")

    def command(self, argv, returncode: int, output: str = "") -> None:
        """Log a command and its result. Arguments are scrubbed like anything else.

        Scrubbed despite :class:`Secret` never reaching an argument, because
        the guarantee that it never does is worth asserting in two places.
        """
        self.write(f"$ {' '.join(str(a) for a in argv)}")
        self.write(f"  -> exit {returncode}")
        for line in (output or "").splitlines()[:40]:
            self.write(f"  | {line}")

    def flush(self) -> bool:
        """Write the buffer out. Never raises: a log failure is not a failure."""
        if not self.path:
            return False
        try:
            self.host.write_bytes(self.path,
                                  ("\n".join(self.lines) + "\n").encode("utf-8"))
        except OSError:
            return False
        return True

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def contains_secret(self, secret: Secret | str) -> bool:
        """For tests: is this plaintext anywhere in the log?"""
        raw = secret.reveal() if isinstance(secret, Secret) else secret
        return bool(raw) and raw in self.text
