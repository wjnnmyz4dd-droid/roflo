"""Receiving payment on a chain, without ever holding a key.

The property this module exists to protect: Solvent records an address
and recognises a confirmation somebody else observed. It never signs,
never sends, and never converts. An address is a string here, and a
string cannot spend.

The second property: an address it cannot fully verify is an address it
refuses. A mistyped destination loses the money with no recourse, and
there is no support desk on a chain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from solvent.errors import FailClosed
from solvent.payments import (
    CONVERSION_ADVISORY, CONVERSION_AUTHORISED, CONVERSION_OFF,
    CRYPTO_NETWORKS, DEFAULT_CONFIRMATIONS, ConversionPolicy, CryptoRail,
    CryptoReceipt, EventKind, address_problem,
)

#: A real bech32 address from BIP-173's own test vectors.
GOOD_BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
GOOD_EVM = "0x" + "a" * 40
SECRET = "watcher-shared-secret-not-a-credential"


class TheWalletFieldStartsEmpty(unittest.TestCase):

    def test_the_shipped_receipt_is_blank(self):
        receipt = CryptoReceipt()
        self.assertEqual(receipt.address, "")
        self.assertEqual(receipt.network, "")

    def test_blank_is_a_working_state_that_refuses(self):
        self.assertFalse(CryptoReceipt().configured)
        self.assertIn("no crypto receipt address", CryptoReceipt().problem())

    def test_nothing_fills_it_in_for_the_owner(self):
        """No default, no example, no placeholder that could be mistaken
        for a destination. An invented address is somebody's money gone."""
        import dataclasses

        for field in dataclasses.fields(CryptoReceipt):
            if field.name == "minimum_confirmations":
                continue
            with self.subTest(field=field.name):
                self.assertEqual(field.default, "")


class AnAddressItCannotCheckIsRefused(unittest.TestCase):

    def test_a_good_bech32_address_passes(self):
        self.assertEqual(address_problem("bitcoin", GOOD_BTC), "")

    def test_one_wrong_character_is_caught(self):
        """The whole reason the checksum is implemented rather than the
        length being counted."""
        typo = GOOD_BTC[:-1] + ("x" if GOOD_BTC[-1] != "x" else "y")
        self.assertIn("checksum", address_problem("bitcoin", typo))

    def test_a_legacy_bitcoin_address_is_refused_not_guessed(self):
        legacy = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
        self.assertIn("bech32", address_problem("bitcoin", legacy))

    def test_a_lowercase_evm_address_passes(self):
        self.assertEqual(address_problem("ethereum", GOOD_EVM), "")

    def test_a_mixed_case_evm_address_is_refused(self):
        """EIP-55 needs Keccak-256, which is not Python's sha3_256 and is
        not in the standard library. Unverifiable means refused."""
        mixed = "0x" + "aB" * 20
        problem = address_problem("ethereum", mixed)
        self.assertIn("Keccak", problem)
        self.assertIn("lower-case", problem)

    def test_a_truncated_address_is_refused(self):
        self.assertTrue(address_problem("ethereum", "0x" + "a" * 39))

    def test_an_unknown_network_is_refused(self):
        self.assertIn("not a network", address_problem("dogecoin", GOOD_EVM))

    def test_every_declared_network_declares_its_assets(self):
        for network, assets in CRYPTO_NETWORKS.items():
            with self.subTest(network=network):
                self.assertTrue(assets)
                self.assertIn(network, DEFAULT_CONFIRMATIONS)
                self.assertGreater(DEFAULT_CONFIRMATIONS[network], 0)


class ASettledPaymentNeedsConfirmations(unittest.TestCase):

    def receipt(self, **over):
        fields = {"network": "bitcoin", "asset": "BTC", "address": GOOD_BTC,
                  "minimum_confirmations": 3}
        fields.update(over)
        return CryptoReceipt(**fields)

    def test_zero_confirmations_is_not_a_configured_receipt(self):
        problem = self.receipt(minimum_confirmations=0).problem()
        self.assertIn("replaced", problem)

    def test_an_asset_the_network_does_not_settle_is_refused(self):
        self.assertIn("not recorded as settling",
                      self.receipt(asset="DOGE").problem())

    def test_a_complete_receipt_is_configured(self):
        self.assertTrue(self.receipt().configured)


class TheRailVerifiesOrRefuses(unittest.TestCase):

    def receipt(self):
        return CryptoReceipt(network="bitcoin", asset="BTC",
                             address=GOOD_BTC, minimum_confirmations=3)

    def delivery(self, secret=SECRET, **over):
        body = {"tx_hash": "abc123", "to_address": GOOD_BTC,
                "confirmations": 6, "amount_cents": 40_000,
                "currency": "usd", "livemode": True,
                "solvent_job_id": "job_1", "observed_rate": "1 BTC = $X"}
        body.update(over)
        raw = json.dumps(body).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return raw, {"X-DeskPilot-Signature": signature}

    def test_an_unconfigured_receipt_verifies_nothing(self):
        rail = CryptoRail(CryptoReceipt())
        raw, headers = self.delivery()
        with self.assertRaises(FailClosed) as caught:
            rail.verify_event(raw, headers, SECRET)
        self.assertIn("no crypto receipt address", str(caught.exception))

    def test_no_watcher_secret_verifies_nothing(self):
        rail = CryptoRail(self.receipt())
        raw, headers = self.delivery()
        with self.assertRaises(FailClosed) as caught:
            rail.verify_event(raw, headers, "")
        self.assertIn("watcher secret", str(caught.exception))

    def test_a_forged_signature_is_refused(self):
        rail = CryptoRail(self.receipt())
        raw, _ = self.delivery()
        with self.assertRaises(FailClosed):
            rail.verify_event(raw, {"X-DeskPilot-Signature": "0" * 64},
                              SECRET)

    def test_a_signature_from_the_wrong_secret_is_refused(self):
        rail = CryptoRail(self.receipt())
        raw, headers = self.delivery(secret="some-other-secret")
        with self.assertRaises(FailClosed):
            rail.verify_event(raw, headers, SECRET)

    def test_a_payment_to_another_address_is_not_revenue(self):
        """Correctly signed, genuinely on-chain, and somebody else's."""
        rail = CryptoRail(self.receipt())
        other = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
        raw, headers = self.delivery(to_address=other)
        with self.assertRaises(FailClosed) as caught:
            rail.verify_event(raw, headers, SECRET)
        self.assertIn("somebody else's money", str(caught.exception))

    def test_too_few_confirmations_is_refused(self):
        rail = CryptoRail(self.receipt())
        raw, headers = self.delivery(confirmations=1)
        with self.assertRaises(FailClosed) as caught:
            rail.verify_event(raw, headers, SECRET)
        self.assertIn("replaced", str(caught.exception))

    def test_a_confirmation_with_no_amount_establishes_nothing(self):
        """Solvent cannot price an asset. The watcher states the cents it
        observed, or there is no revenue to record."""
        rail = CryptoRail(self.receipt())
        raw, headers = self.delivery(amount_cents=0)
        with self.assertRaises(FailClosed) as caught:
            rail.verify_event(raw, headers, SECRET)
        self.assertIn("cannot price an asset", str(caught.exception))

    def test_a_good_confirmation_becomes_an_ordinary_payment_event(self):
        rail = CryptoRail(self.receipt())
        raw, headers = self.delivery()
        event = rail.verify_event(raw, headers, SECRET)
        self.assertEqual(event.kind, EventKind.CUSTOMER_PAID)
        self.assertEqual(event.amount_cents, 40_000)
        self.assertEqual(event.job_id, "job_1")
        self.assertEqual(event.rail, "crypto")
        self.assertFalse(event.is_test_money)

    def test_testnet_coins_are_not_revenue(self):
        rail = CryptoRail(self.receipt())
        raw, headers = self.delivery(livemode=False)
        event = rail.verify_event(raw, headers, SECRET)
        self.assertTrue(event.is_test_money)


class ConversionIsADecisionNotAnAction(unittest.TestCase):

    def test_it_is_off_by_default(self):
        self.assertEqual(ConversionPolicy().state, CONVERSION_OFF)
        self.assertFalse(ConversionPolicy().may_record_conversion)

    def test_authorising_without_a_provider_is_refused(self):
        policy = ConversionPolicy(state=CONVERSION_AUTHORISED)
        self.assertIn("does not", policy.problem())
        self.assertFalse(policy.may_record_conversion)

    def test_authorising_with_a_provider_permits_recording_only(self):
        policy = ConversionPolicy(state=CONVERSION_AUTHORISED,
                                  provider="the owner's exchange")
        self.assertEqual(policy.problem(), "")
        self.assertTrue(policy.may_record_conversion)

    def test_advisory_records_nothing(self):
        policy = ConversionPolicy(state=CONVERSION_ADVISORY)
        self.assertFalse(policy.may_record_conversion)

    def test_an_unknown_state_is_refused(self):
        self.assertTrue(ConversionPolicy(state="ON").problem())


class ThePolicyRecordsAnAddressAndNothingElse(unittest.TestCase):

    def setUp(self):
        import tempfile

        from solvent.harness import OWNER, Solvent

        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/crypto.db")
        self.addCleanup(self.close)
        self.owner = OWNER

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def test_the_shipped_policy_has_no_address(self):
        self.assertEqual(self.s.policy.crypto_receipt().address, "")

    def test_conversion_ships_off(self):
        self.assertEqual(self.s.policy.conversion_policy().state,
                         CONVERSION_OFF)

    def test_a_typo_is_refused_before_it_is_stored(self):
        typo = GOOD_BTC[:-1] + ("x" if GOOD_BTC[-1] != "x" else "y")
        with self.assertRaises(FailClosed):
            self.s.policy.record_crypto_receipt(
                owner_identity=self.owner, network="bitcoin", asset="BTC",
                address=typo)
        self.assertEqual(self.s.policy.crypto_receipt().address, "",
                         "a refused address was stored anyway")

    def test_a_good_address_is_recorded_with_the_network_default(self):
        self.s.policy.record_crypto_receipt(
            owner_identity=self.owner, network="bitcoin", asset="BTC",
            address=GOOD_BTC)
        receipt = self.s.policy.crypto_receipt()
        self.assertEqual(receipt.address, GOOD_BTC)
        self.assertEqual(receipt.minimum_confirmations,
                         DEFAULT_CONFIRMATIONS["bitcoin"])

    def test_only_the_owner_may_record_one(self):
        with self.assertRaises(FailClosed):
            self.s.policy.record_crypto_receipt(
                owner_identity="application", network="bitcoin",
                asset="BTC", address=GOOD_BTC)

    def test_recording_an_address_stores_no_credential(self):
        """What goes into policy is an address. Nothing else about it."""
        self.s.policy.record_crypto_receipt(
            owner_identity=self.owner, network="bitcoin", asset="BTC",
            address=GOOD_BTC)
        record = self.s.policy.get("payment", "crypto", default={})
        self.assertEqual(sorted(record),
                         ["address", "asset", "minimum_confirmations",
                          "network"])


class NothingHereCanSpend(unittest.TestCase):
    """Checked structurally, because it is the whole premise.

    Over identifiers rather than over the file's text. The first version
    of this grepped the source and failed on the word "broadcast" in a
    comment that says there is no broadcast -- a test that cannot tell a
    promise from its violation.
    """

    @staticmethod
    def identifiers():
        import ast
        import pathlib

        tree = ast.parse(
            pathlib.Path("solvent/payments.py").read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, ast.Attribute):
                found.add(node.attr)
            elif isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add((node.module or "").split(".")[0])
                found.update(a.name for a in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                found.add(node.name)
        return found

    def test_nothing_signs_or_sends(self):
        names = self.identifiers()
        for forbidden in ("private_key", "privatekey", "seed_phrase",
                          "mnemonic", "sign_transaction", "sendTransaction",
                          "send_transaction", "broadcast", "eth_sendRaw",
                          "from_key", "sign"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)

    def test_no_network_client_is_imported(self):
        names = self.identifiers()
        for forbidden in ("requests", "socket", "urlopen", "web3", "httpx",
                          "urllib", "subprocess"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_guard_would_notice_a_violation(self):
        """The test above is only worth having if it can fail."""
        names = self.identifiers()
        self.assertIn("verify_event", names,
                      "the identifier scan found nothing, so its absences "
                      "prove nothing")


if __name__ == "__main__":
    unittest.main()
