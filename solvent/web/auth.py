"""Sessions, CSRF and the owner's web password. No secret reaches the browser.

Deliberately *not* the Owner Channel. That channel authenticates an *approval* —
a signed, scoped, single-use grant over one action — and its key is the thing a
compromised web process must not have. Logging in is a different question, and
answering it with the same key would put the key in the process an attacker
reaches first.

So: a password the owner sets, stored only as a PBKDF2 hash, in the same
protected environment file as everything else. Sessions live in memory and
nowhere else, which costs a logout on restart and removes a session store to
attack. The owner key is never read in this package.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

#: Where the owner's web password hash comes from. A hash, never a password.
PASSWORD_HASH_ENV = "SOLVENT_WEB_PASSWORD_HASH"

#: PBKDF2 cost. High enough that a leaked hash is not a password list.
ITERATIONS = 600_000
#: How long a session lasts without use, and at most, in seconds.
IDLE_TIMEOUT = 30 * 60
ABSOLUTE_TIMEOUT = 12 * 60 * 60
#: Failed attempts before a source is refused, and for how long.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

#: Actions that need the owner to re-enter their password even inside a live
#: session. Each one is a thing a stolen session should not be able to do on its
#: own, and re-authentication is the cheapest boundary that stops it.
REAUTH_REQUIRED = frozenset({"halt", "resume"})


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """``pbkdf2$iterations$salt$digest``. The only form ever stored."""
    if len(password) < 12:
        raise ValueError("a web password shorter than 12 characters is not one")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str, encoded: str) -> bool:
    """Constant-time comparison against a stored hash. False on anything odd."""
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$")
    except (ValueError, AttributeError):
        return False
    if scheme != "pbkdf2" or not iterations.isdigit():
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                    int(iterations))
    return hmac.compare_digest(candidate, expected)


def configured_hash(env: dict | None = None) -> str:
    return (env if env is not None else os.environ).get(PASSWORD_HASH_ENV, "")


class Sessions:
    """In-memory sessions with idle and absolute expiry, and a CSRF token each."""

    def __init__(self, *, now=time.time) -> None:
        self._sessions: dict[str, dict] = {}
        self._failures: dict[str, list] = {}
        self._now = now

    # ------------------------------------------------------------- lockout
    def locked_out(self, source: str) -> tuple[bool, int]:
        attempts = [t for t in self._failures.get(source, [])
                    if self._now() - t < LOCKOUT_SECONDS]
        self._failures[source] = attempts
        if len(attempts) >= MAX_FAILURES:
            return True, int(LOCKOUT_SECONDS - (self._now() - attempts[0]))
        return False, 0

    def record_failure(self, source: str) -> None:
        self._failures.setdefault(source, []).append(self._now())

    def clear_failures(self, source: str) -> None:
        self._failures.pop(source, None)

    # ------------------------------------------------------------- sessions
    def create(self, *, source: str = "") -> tuple[str, str]:
        """``(session_id, csrf_token)``. Both unguessable, neither derived from
        the other — a CSRF token that could be computed from a session id would
        not be a second factor at all."""
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        moment = self._now()
        self._sessions[session_id] = {
            "csrf": csrf, "created": moment, "seen": moment, "source": source,
            "reauth_at": moment,
        }
        return session_id, csrf

    def get(self, session_id: str) -> dict | None:
        """The session, refreshed — or None if absent or expired."""
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        moment = self._now()
        if (moment - session["seen"] > IDLE_TIMEOUT
                or moment - session["created"] > ABSOLUTE_TIMEOUT):
            self._sessions.pop(session_id, None)
            return None
        session["seen"] = moment
        return session

    def valid_csrf(self, session_id: str, token: str) -> bool:
        session = self.get(session_id)
        if session is None or not token:
            return False
        return hmac.compare_digest(session["csrf"], token)

    def note_reauth(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session["reauth_at"] = self._now()

    def destroy(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def destroy_all(self) -> None:
        self._sessions.clear()

    def count(self) -> int:
        return len([s for s in list(self._sessions) if self.get(s)])
