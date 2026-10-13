"""Credential shapes the redactor must catch whatever they are called.

Written after a failing-transport injection put a GitHub-shaped token into
the append-only submission log: the exception quoted "?key=<token>", the
label rule matched "token" but not "key", and no shape rule covered
GitHub at all -- only Stripe had one.

The literals here are assembled from fragments at import. A credential-
shaped constant in a source file is blocked by the repository's own
secret scan (correctly), and writing one to make a point about secrets
would be the wrong lesson.
"""

from __future__ import annotations

import unittest

from solvent.resilience import redact

#: Assembled, never written whole. See the module docstring.
SHAPES = {
    "github classic": "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4",
    "github fine-grained": "github" + "_pat_" + "11ABCDEFG0" + "abcdefghij" * 2,
    "slack bot": "xo" + "xb-" + "1234567890-" + "0987654321-" + "AbCdEfGhIj",
    "aws access key": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "anthropic": "sk-" + "ant-" + "api03-" + "AbCdEfGhIjKlMnOpQrSt",
    "google": "AI" + "za" + "SyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",
    "jwt": ("ey" + "JhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"),
}


class EveryShapeIsCaught(unittest.TestCase):

    def test_each_provider_shape_is_redacted_bare(self):
        """Bare: no label, no scheme. This is the case the label rules
        structurally cannot catch."""
        for name, secret in SHAPES.items():
            with self.subTest(shape=name):
                out = redact(f"request failed: {secret}")
                self.assertNotIn(secret, out)
                self.assertIn("REDACTED", out)

    def test_each_shape_is_redacted_under_an_unhelpful_label(self):
        for name, secret in SHAPES.items():
            for label in ("key", "k", "id", "value", "q"):
                with self.subTest(shape=name, label=label):
                    out = redact(f"GET https://x.test/a?{label}={secret}")
                    self.assertNotIn(secret, out)


class TheGuardAgainstOverRedacting(unittest.TestCase):
    """A redactor that eats the diagnostic protects nothing worth having."""

    def test_an_ordinary_key_equals_word_survives(self):
        for text in ("key=customer_id", "key=name", "sig=abc",
                     "auth=none", "session=closed",
                     "the join key=invoice_number was missing"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)

    def test_a_column_name_that_merely_looks_long_survives(self):
        text = "key=monthly_recurring_revenue_usd"
        self.assertEqual(redact(text), text,
                         "a long column name is not a credential")

    def test_a_labelled_credential_shaped_value_is_still_caught(self):
        secret = "A1b2C3d4E5f6G7h8I9j0"
        self.assertNotIn(secret, redact(f"key={secret}"))


class WhatWasAlreadyCaughtStaysCaught(unittest.TestCase):
    """Regression fence around the rules that existed before."""

    def test_bearer_tokens(self):
        token = "AbCdEfGhIjKlMnOp"
        self.assertNotIn(token, redact(f"Authorization: Bearer {token}"))

    def test_stripe(self):
        secret = "sk" + "_live_" + "AbCdEfGhIjKlMnOp"
        self.assertNotIn(secret, redact(f"charge failed for {secret}"))

    def test_long_hex(self):
        secret = "a1b2c3d4" * 6
        self.assertNotIn(secret, redact(f"signature {secret}"))


if __name__ == "__main__":
    unittest.main()
