"""The two connection cards, and the claim they must never make.

DeskPilot cannot know that a mailbox works or that a Stripe account
is attached: both are facts about a credential in somebody else's
store and a message or payment that actually moved. A card reading
CONNECTED on DeskPilot's own say-so would be the most confidently
wrong thing on the page, so the state comes from the owner-governed
policy ladders and nothing here can advance one.
"""

from __future__ import annotations

import unittest

from solvent import connections as conn
from solvent.web.intents import CONSEQUENTIAL, INTENTS


class Policy:
    def __init__(self, **values):
        self._values = values

    def get(self, *path, default=None):
        return self._values.get(".".join(path), default)


class Broken:
    def get(self, *path, default=None):
        raise RuntimeError("the database went away")


class BothCardsStartDisconnected(unittest.TestCase):

    def test_a_fresh_install_shows_neither_as_connected(self):
        for card in conn.cards(Policy()):
            with self.subTest(card=card.key):
                self.assertIs(card.state, conn.State.NOT_CONNECTED)

    def test_each_card_offers_the_button_that_connects_it(self):
        labels = {c.key: c.action_label for c in conn.cards(Policy())}
        self.assertEqual(labels["email"], "CONNECT BUSINESS EMAIL")
        self.assertEqual(labels["stripe"], "CONNECT STRIPE")

    def test_both_buttons_write_registered_consequential_intents(self):
        """The owner's approval is the existing mechanism, not a new one."""
        for card in conn.cards(Policy()):
            with self.subTest(card=card.key):
                self.assertIn(card.action_verb, INTENTS)
                self.assertIs(INTENTS[card.action_verb][0], CONSEQUENTIAL)

    def test_stripe_reuses_the_existing_payment_rail(self):
        """Not a second payment system."""
        self.assertEqual(conn.stripe_card(Policy()).action_verb,
                         "advance_payment_rail")


class TheStateFollowsThePolicyAndNothingElse(unittest.TestCase):

    def test_a_configured_rail_reads_connected(self):
        card = conn.stripe_card(Policy(**{"payment.approved_rail": "stripe",
                                          "payment.rail_state": "CONFIGURED"}))
        self.assertIs(card.state, conn.State.CONNECTED)

    def test_a_selected_but_unconfigured_rail_is_not_connected(self):
        card = conn.stripe_card(Policy(**{"payment.approved_rail": "stripe",
                                          "payment.rail_state": "SELECTED"}))
        self.assertIs(card.state, conn.State.NOT_CONNECTED)
        self.assertTrue(card.owner_must_act_elsewhere)

    def test_another_rail_does_not_read_as_stripe_connected(self):
        card = conn.stripe_card(Policy(**{"payment.approved_rail": "crypto",
                                          "payment.rail_state": "CONFIGURED"}))
        self.assertIs(card.state, conn.State.NOT_CONNECTED)

    def test_a_configured_mailbox_reads_connected(self):
        card = conn.email_card(Policy(**{"email.provider": "Microsoft 365",
                                         "email.state": "CONFIGURED"}))
        self.assertIs(card.state, conn.State.CONNECTED)

    def test_engineering_ready_is_not_connected(self):
        """Our side being built is not an account being attached."""
        card = conn.email_card(Policy(**{"email.provider": "Gmail",
                                         "email.state": "ENGINEERING_READY"}))
        self.assertIs(card.state, conn.State.NOT_CONNECTED)

    def test_an_unreadable_policy_renders_error_not_connected(self):
        for card in conn.cards(Broken()):
            with self.subTest(card=card.key):
                self.assertIs(card.state, conn.State.ERROR)


class ConnectingEnablesNothing(unittest.TestCase):

    def test_both_configured_still_does_not_enable_live_actions(self):
        policy = Policy(**{"payment.approved_rail": "stripe",
                           "payment.rail_state": "LIVE_VERIFIED",
                           "email.provider": "Gmail",
                           "email.state": "LIVE_VERIFIED",
                           "egress.simulation_only": True})
        self.assertFalse(conn.live_actions_enabled(policy))

    def test_the_section_says_so_in_words(self):
        note = conn.summary(Policy())["note"]
        self.assertIn("does not enable", note)
        self.assertIn("simulation", note.lower())

    def test_no_card_ever_asks_for_a_password(self):
        import inspect

        source = inspect.getsource(conn)
        self.assertIn("never asks for a password", source)
        for word in ("secret_key", "api_key", "password=", "sk_live"):
            self.assertNotIn(word, source)


class TheSectionActuallyRenders(unittest.TestCase):
    """A card nobody can see is not an interface."""

    def test_the_command_centre_renders_both_cards(self):
        from solvent.web import app as app_module

        class Read:
            def policy_value(self, *path, default=None):
                return default

        html = app_module._connection_cards(Read())
        self.assertIn("Business Email", html)
        self.assertIn("Stripe Payments", html)
        self.assertIn("CONNECT BUSINESS EMAIL", html)
        self.assertIn("CONNECT STRIPE", html)
        self.assertIn("NOT CONNECTED", html)

    def test_the_rendered_section_carries_the_warning(self):
        from solvent.web import app as app_module

        class Read:
            def policy_value(self, *path, default=None):
                return default

        self.assertIn("does not enable",
                      app_module._connection_cards(Read()))

    def test_an_unreadable_policy_does_not_break_the_page(self):
        from solvent.web import app as app_module

        class Read:
            def policy_value(self, *path, default=None):
                raise RuntimeError("gone")

        html = app_module._connection_cards(Read())
        self.assertIn("ERROR", html)


if __name__ == "__main__":
    unittest.main()
