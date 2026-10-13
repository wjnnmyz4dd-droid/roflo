"""K-LEAN: what a failing source writes down about why it failed.

When a source's response cannot be parsed, DeskPilot records why. That
reason comes from an exception, and an exception raised while reading an
HTTP response routinely carries a slice of that response — which, for an
API that echoes the request back in its error body, is where a credential
lives.

Two places receive it: `work_sources.failure_reason`, which the Control
Centre renders on the sources page, and the audit log, which is append-only
and by construction cannot be redacted afterwards.

`solvent/discovery.py` used `redact` exactly nowhere. Keyed on what a secret
looks like, not on what a field is called.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent.discovery import Compliance, FixtureSource, Readiness
from solvent.harness import OWNER, Solvent

#: Assembled at import, never written as a literal: this repository refuses
#: to carry a credential-shaped string, and so does its host.
SECRETS = {
    "stripe": "sk" + "_live_" + "AbCdEf0123456789AbCdEfGh",
    "api-key-pair": "api_" + "key=" + "9f8e7d6c5b4a39281706f5e4",
    "bearer": "Bearer " + "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
}


def exploding(secret: str):
    class Exploding(FixtureSource):
        is_fixture = False

        def parse(self, payload):
            raise ValueError(f"upstream rejected the request: {secret}")

        def request(self):
            return None

    return Exploding


class ACredentialInAnUpstreamError(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/leak.db")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def fail_a_poll(self, secret: str):
        cls = exploding(secret)
        self.s.discovery.register_source(
            cls("boom", []), owner_identity=OWNER,
            readiness=Readiness.PERMITTED_AUTOMATION,
            compliance=Compliance.PERMITTED, determination="leak probe")
        self.s.policy.amend({"discovery": {"approved_sources": ["boom"]}},
                            OWNER, "leak probe")
        try:
            self.s.discovery.poll("boom")
        except Exception:
            pass

    def everything_written_down(self) -> str:
        out = []
        for table in ("work_sources", "audit_log"):
            for row in self.s.store.raw_readonly(f"SELECT * FROM {table}"):
                for key in row.keys():
                    if isinstance(row[key], str):
                        out.append(row[key])
        return "\n".join(out)

    def test_no_credential_shape_survives_a_parse_failure(self):
        for name, secret in SECRETS.items():
            with self.subTest(credential=name):
                self.setUp()
                self.fail_a_poll(secret)
                self.assertNotIn(
                    secret, self.everything_written_down(),
                    f"the {name} credential from an upstream error body was "
                    "written verbatim into the source record or the audit "
                    "log, and the audit log cannot be redacted afterwards")

    def test_the_source_still_says_why_it_failed(self):
        """Redaction must not cost the diagnostic its usefulness."""
        self.fail_a_poll(SECRETS["stripe"])
        state = self.s.discovery.source_state("boom")
        reason = str(state["failure_reason"])
        self.assertIn("ValueError", reason)
        self.assertIn("upstream rejected", reason)

    def test_the_source_is_marked_failed_not_quietly_healthy(self):
        self.fail_a_poll(SECRETS["bearer"])
        state = self.s.discovery.source_state("boom")
        self.assertNotEqual(state["health"], "HEALTHY")

    def test_an_ordinary_failure_is_unchanged_by_redaction(self):
        self.fail_a_poll("the JSON ended unexpectedly at line 4")
        reason = str(self.s.discovery.source_state("boom")["failure_reason"])
        self.assertIn("ended unexpectedly at line 4", reason,
                      "redaction has started eating ordinary diagnostics")


if __name__ == "__main__":
    unittest.main()
