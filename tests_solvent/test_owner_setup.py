"""§9, §34, §35: the owner's configuration, and the ways it could escape.

Everything the owner provides is either something a client may legitimately see
(the name they contract under), something the host must protect (a signing key),
or something Solvent should never hold at all (a tax identifier). This file
establishes which is which by trying to make each one leak.

TEST-ONLY values throughout. Nothing here generates, reads or needs a real
secret, and every value is labelled so that anything which ever surfaces in a
log or a database is unmistakably a fixture.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

from solvent import cli, payments
from solvent.errors import FailClosed
from solvent.harness import (
    OWNER, STRIPE_SECRET_ENV, Solvent, ingest_payment_spool,
)
from solvent.owner import KEY_ENV, OwnerChannel
from tests_solvent.fixtures import TEST_ONLY_OWNER_KEY

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Values a leak test looks for. Distinctive enough that a substring search is
#: meaningful, and labelled so a real one could never be mistaken for them.
TEST_WEBHOOK_SECRET = "whsec_TEST-ONLY-not-a-real-endpoint-secret-0123456789"
TEST_TAX_REFERENCE = "TEST-ONLY-TAXREF-99887766-NOT-REAL"
TEST_LEGAL_NAME = "TEST-ONLY Owner Name"
TEST_EMAIL = "test-only@example.invalid"


def database_bytes(db: str) -> bytes:
    """Every byte SQLite is holding for this database, not just the main file.

    The store runs in WAL mode, so a recent write lives in ``<db>-wal`` and the
    main file can still be a 4 KB header. Searching only ``<db>`` for a leaked
    secret therefore searches an almost empty file and passes whatever is in it
    — which is how these tests first passed while proving nothing at all.
    """
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        path = pathlib.Path(db + suffix)
        if path.exists():
            blob += path.read_bytes()
    return blob


def run_cli(*argv) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(list(argv))
    return code, buffer.getvalue()


def configured(db: str) -> Solvent:
    """A Solvent with every owner-configurable thing done, via the CLI."""
    run_cli("setup", "capability", "--db", db)
    run_cli("setup", "work-source", "--db", db)
    run_cli("setup", "model", "--db", db)
    run_cli("setup", "stripe", "SELECTED", "--db", db)
    run_cli("setup", "stripe", "ENGINEERING_READY", "--db", db)
    run_cli("setup", "contracting", "--db", db,
            "--legal-name", TEST_LEGAL_NAME, "--email", TEST_EMAIL,
            "--tax-reference-provisioned")
    return Solvent(db)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = str(pathlib.Path(tempfile.mkdtemp()) / "solvent.db")


class TheOwnerKeyCannotEscape(Base):
    """A signing key that turns up anywhere is not a signing key any more."""

    def setUp(self):
        super().setUp()
        self.s = configured(self.db)
        self.channel = OwnerChannel(self.s.store, self.s.audit, self.s.policy,
                                    key=TEST_ONLY_OWNER_KEY)
        self.secret = TEST_ONLY_OWNER_KEY.decode()

    def test_it_is_not_in_the_database_file(self):
        """The whole file, as bytes. Not a query — a query only finds the
        columns somebody thought to look in."""
        from solvent.owner import KEY_ENV as _  # noqa: F401

        self.channel.issue(owner_identity=OWNER, subject="deliver",
                           action_class=__import__(
                               "solvent.types", fromlist=["ActionClass"]
                           ).ActionClass.C2_EXTERNAL_COMMUNICATION,
                           job_id="J1", max_cents=0)
        self.s.store.close()
        self.assertNotIn(self.secret.encode(), database_bytes(self.db))

    def test_the_database_search_would_find_a_value_that_was_there(self):
        """Guards every "not in the database" test in this file. They search the
        WAL as well as the main file precisely because the first version did
        not, and passed against a 4 KB header."""
        self.s.policy.amend({"governance": {"canary": "TEST-ONLY-CANARY-7781"}},
                            OWNER, "prove the search reaches stored data")
        self.s.store.close()
        self.assertIn(b"TEST-ONLY-CANARY-7781", database_bytes(self.db))

    def test_it_is_not_in_the_readiness_output(self):
        _, out = run_cli("readiness", "--db", self.db)
        self.assertNotIn(self.secret, out)

    def test_it_is_not_in_the_setup_check_output(self):
        os.environ[KEY_ENV] = self.secret
        try:
            _, out = run_cli("setup", "check", "--db", self.db)
        finally:
            os.environ.pop(KEY_ENV, None)
        self.assertNotIn(self.secret, out)
        self.assertIn("OWNER_KEY", out)

    def test_the_check_still_says_whether_the_key_is_there(self):
        """Guards the test above: a command that reveals nothing at all,
        including whether the key exists, would be useless."""
        os.environ[KEY_ENV] = self.secret
        try:
            _, present = run_cli("setup", "check", "--db", self.db)
        finally:
            os.environ.pop(KEY_ENV, None)
        _, absent = run_cli("setup", "check", "--db", self.db)
        self.assertIn("present and valid", present)
        self.assertNotIn("present and valid", absent)

    def test_it_is_not_in_a_crash_report_from_a_failed_approval(self):
        weak = OwnerChannel(self.s.store, self.s.audit, self.s.policy,
                            key=b"hunter2")
        from solvent.types import ActionClass

        with self.assertRaises(FailClosed) as caught:
            weak.issue(owner_identity=OWNER, subject="x",
                       action_class=ActionClass.C3_FINANCIAL_COMMITMENT)
        self.assertNotIn("hunter2", str(caught.exception))

    def test_it_is_not_in_a_backup(self):
        self.s.store.close()
        destination = pathlib.Path(tempfile.mkdtemp())
        copy = destination / "copy.db"
        import sqlite3

        source = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        target = sqlite3.connect(str(copy))
        with target:
            source.backup(target)
        source.close()
        target.close()
        self.assertNotIn(self.secret.encode(), copy.read_bytes())


class TheStripeSecretCannotEscape(Base):
    def setUp(self):
        super().setUp()
        self.s = configured(self.db)
        self.spool = pathlib.Path(tempfile.mkdtemp())

    def deliver(self, body: bytes, headers: dict, name="1.webhook"):
        payments.write_delivery(self.spool / name, headers, body)

    def signed(self, body: bytes, secret=TEST_WEBHOOK_SECRET, skew=0) -> dict:
        import hashlib
        import hmac
        import time

        ts = int(time.time()) + skew
        mac = hmac.new(secret.encode(), f"{ts}.".encode() + body,
                       hashlib.sha256).hexdigest()
        return {"Stripe-Signature": f"t={ts},v1={mac}"}

    def event(self, event_id="evt_test", livemode=False) -> bytes:
        return json.dumps({
            "id": event_id, "type": "payment_intent.succeeded",
            "livemode": livemode,
            "data": {"object": {"amount_received": 5000, "currency": "usd",
                                "metadata": {"job_id": "J1"}}}}).encode()

    def test_a_refusal_does_not_quote_the_secret(self):
        self.deliver(self.event(), {"Stripe-Signature": "t=1,v1=00"})
        results = ingest_payment_spool(self.s, spool=str(self.spool),
                                       secret=TEST_WEBHOOK_SECRET)
        self.assertEqual(results[0]["outcome"], "REFUSED")
        self.assertNotIn(TEST_WEBHOOK_SECRET, results[0]["why"])

    def test_the_secret_is_not_in_the_audit_after_a_verified_event(self):
        self.deliver(self.event(), self.signed(self.event()))
        ingest_payment_spool(self.s, spool=str(self.spool),
                             secret=TEST_WEBHOOK_SECRET)
        text = repr([dict(e) for e in self.s.audit.events()])
        self.assertNotIn(TEST_WEBHOOK_SECRET, text)

    def test_the_secret_is_not_in_the_database(self):
        self.deliver(self.event(), self.signed(self.event()))
        ingest_payment_spool(self.s, spool=str(self.spool),
                             secret=TEST_WEBHOOK_SECRET)
        self.s.store.close()
        self.assertNotIn(TEST_WEBHOOK_SECRET.encode(), database_bytes(self.db))

    def test_it_is_read_from_the_environment_and_never_written_down(self):
        import inspect

        source = inspect.getsource(ingest_payment_spool)
        self.assertIn("STRIPE_SECRET_ENV", source)
        self.assertIn("os.environ.get", source)
        for stored in ("policy.amend", "_db.execute", "INSERT"):
            with self.subTest(stored=stored):
                self.assertNotIn(stored, source)

    def test_the_secret_never_reaches_the_audit_record_it_writes(self):
        import inspect

        source = inspect.getsource(ingest_payment_spool)
        audit_call = source[source.index("s.audit.record("):]
        self.assertNotIn("secret", audit_call)


class TheSpoolIsTheOnlyWayInAndItVerifies(Base):
    """The rail could not be reached at all: `bind` is denied process-wide, so
    nothing could deliver the bytes it verifies. These establish that the spool
    closes that without weakening what the rail decides."""

    def setUp(self):
        super().setUp()
        self.s = configured(self.db)
        self.spool = pathlib.Path(tempfile.mkdtemp())
        self.helper = TheStripeSecretCannotEscape("run")
        self.helper.spool = self.spool

    def ingest(self):
        return ingest_payment_spool(self.s, spool=str(self.spool),
                                    secret=TEST_WEBHOOK_SECRET)

    def test_a_genuine_event_reaches_the_ledger(self):
        body = self.helper.event()
        payments.write_delivery(self.spool / "1.webhook",
                                self.helper.signed(body), body)
        results = self.ingest()
        self.assertEqual(len(results), 1)
        self.assertNotEqual(results[0]["outcome"], "REFUSED")
        self.assertTrue(self.s.ledger.payment_events())

    def test_an_unsigned_delivery_is_refused(self):
        body = self.helper.event()
        payments.write_delivery(self.spool / "1.webhook", {}, body)
        self.assertEqual(self.ingest()[0]["outcome"], "REFUSED")

    def test_a_delivery_signed_with_another_secret_is_refused(self):
        body = self.helper.event()
        payments.write_delivery(
            self.spool / "1.webhook",
            self.helper.signed(body, secret="whsec_TEST-ONLY-wrong"), body)
        self.assertEqual(self.ingest()[0]["outcome"], "REFUSED")

    def test_a_replayed_timestamp_is_refused(self):
        body = self.helper.event()
        payments.write_delivery(self.spool / "1.webhook",
                                self.helper.signed(body, skew=-100000), body)
        self.assertEqual(self.ingest()[0]["outcome"], "REFUSED")

    def test_a_body_altered_after_signing_is_refused(self):
        body = self.helper.event()
        headers = self.helper.signed(body)
        payments.write_delivery(self.spool / "1.webhook", headers,
                                body.replace(b"5000", b"9999"))
        self.assertEqual(self.ingest()[0]["outcome"], "REFUSED")

    def test_the_same_event_delivered_twice_is_applied_once(self):
        body = self.helper.event()
        headers = self.helper.signed(body)
        payments.write_delivery(self.spool / "1.webhook", headers, body)
        payments.write_delivery(self.spool / "2.webhook", headers, body)
        outcomes = [r["outcome"] for r in self.ingest()]
        self.assertEqual(sum("duplicate" in o for o in outcomes), 1)

    def test_test_mode_money_is_not_revenue(self):
        body = self.helper.event(livemode=False)
        payments.write_delivery(self.spool / "1.webhook",
                                self.helper.signed(body), body)
        self.ingest()
        self.assertEqual(self.s.ledger.real_revenue_cents(), 0)

    def test_a_refused_delivery_is_kept_rather_than_deleted(self):
        body = self.helper.event()
        payments.write_delivery(self.spool / "1.webhook", {}, body)
        self.ingest()
        self.assertTrue(list((self.spool / "rejected").glob("*.webhook")))

    def test_a_half_written_delivery_is_not_read(self):
        """The writer renames into place. A reader that picked up partial files
        would report a truncated body as a rejected payment."""
        (self.spool / "1.webhook.partial").write_bytes(b"{}\nincomplete")
        self.assertEqual(self.ingest(), [])

    def test_an_empty_secret_refuses_rather_than_verifying_vacuously(self):
        body = self.helper.event()
        payments.write_delivery(self.spool / "1.webhook",
                                self.helper.signed(body), body)
        results = ingest_payment_spool(self.s, spool=str(self.spool), secret="")
        self.assertEqual(results[0]["outcome"], "REFUSED")

    def test_the_body_survives_byte_for_byte(self):
        """The signature is over the exact bytes. Any re-encoding either breaks
        verification or verifies something other than what arrived."""
        body = b'{"id":"evt_x","spacing":  [1,  2],"unicode":"\\u00e9"}'
        payments.write_delivery(self.spool / "1.webhook", {"a": "b"}, body)
        _, read_back = payments.read_delivery(self.spool / "1.webhook")
        self.assertEqual(read_back, body)


class TheOwnersIdentityIsHeldProportionately(Base):
    def setUp(self):
        super().setUp()
        self.s = configured(self.db)

    def test_the_name_and_email_are_recorded_because_documents_need_them(self):
        party = self.s.policy.contracting_party()
        self.assertEqual(party["legal_name"], TEST_LEGAL_NAME)
        self.assertEqual(party["email"], TEST_EMAIL)

    def test_no_tax_identifier_can_be_recorded_through_the_cli_at_all(self):
        """The flag takes no value, so there is no argument for a number to
        arrive in. Presence is all Solvent ever learns."""
        self.assertEqual(self.s.policy.contracting_party()["tax_reference"],
                         self.s.policy.ATTESTED)

    def test_a_tax_identifier_passed_directly_is_still_not_stored(self):
        self.s.policy.record_contracting_structure(
            owner_identity=OWNER, structure=self.s.policy.INDIVIDUAL,
            reason="a caller that passes the number anyway",
            tax_reference=TEST_TAX_REFERENCE)
        self.s.store.close()
        self.assertNotIn(TEST_TAX_REFERENCE.encode(), database_bytes(self.db))

    def test_the_identity_is_in_the_database_which_backups_do_include(self):
        """Stated rather than assumed: a backup carries the owner's business
        identity, because it carries the policy document. That is the right
        trade — but it is a fact the owner should know, not discover."""
        self.s.store.close()
        self.assertIn(TEST_LEGAL_NAME.encode(), database_bytes(self.db))


def gitignore_patterns() -> list[str]:
    return [line.strip() for line in (REPO / ".gitignore").read_text().splitlines()
            if line.strip() and not line.startswith("#")]


def is_ignored(relative: str) -> bool:
    """Whether .gitignore covers this path, evaluated the way git orders it.

    Implemented here rather than by running ``git check-ignore`` because
    Solvent's egress hook denies ``subprocess.Popen`` for the whole interpreter
    — correctly, since a child process would escape an in-process control. The
    first version of these tests shelled out, passed on its own, and errored
    inside the full suite as soon as any earlier test had installed the hook.
    """
    import fnmatch

    decision = False
    for pattern in gitignore_patterns():
        negated = pattern.startswith("!")
        candidate = pattern[1:] if negated else pattern
        candidate = candidate.rstrip("/")
        parts = relative.split("/")
        hit = (fnmatch.fnmatch(relative, candidate)
               or any(fnmatch.fnmatch(part, candidate) for part in parts)
               or any(fnmatch.fnmatch("/".join(parts[i:]), candidate)
                      for i in range(len(parts))))
        if hit:
            decision = not negated
    return decision


def repository_files():
    """Every file in the working tree that git would plausibly carry."""
    skip = {".git", ".venv", "venv", "__pycache__", "node_modules",
            ".pytest_cache", ".ruff_cache", "models", "dist", "build"}
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO).as_posix()
        if any(part in skip for part in path.relative_to(REPO).parts):
            continue
        yield relative, path


class GitCannotCarryASecret(unittest.TestCase):
    """.gitignore is not a security boundary. These check it from both sides:
    that the patterns cover what they must, and that nothing in the tree looks
    like a credential regardless of what the patterns say."""

    def test_no_file_in_the_tree_contains_a_credential_shaped_value(self):
        import re

        pattern = re.compile(
            r"(sk_live_[A-Za-z0-9]{8,}|sk_test_[A-Za-z0-9]{8,}"
            r"|whsec_[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
        offenders = []
        for relative, path in repository_files():
            if path.suffix in {".png", ".jpg", ".gguf", ".db", ".safetensors"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for match in pattern.finditer(text):
                window = text[max(0, match.start() - 80):match.end() + 80]
                if "TEST-ONLY" in window:
                    continue
                offenders.append(f"{relative}: {match.group(0)[:14]}…")
        self.assertEqual(offenders, [])

    def test_the_search_would_find_a_credential_if_one_were_there(self):
        """Guards the test above: a pattern that matches nothing passes
        everything."""
        import re

        pattern = re.compile(r"whsec_[A-Za-z0-9]{16,}")
        # Assembled at run time. Written as a literal it would sit in this file
        # and the scan above would report it -- which it did, on the first run.
        specimen = "whsec" + "_" + ("abcdefgh" * 3)
        self.assertTrue(pattern.search(specimen))

    def test_every_plausible_secret_file_location_is_ignored(self):
        for candidate in (".env", "solvent.env", "deploy/solvent.env",
                          "secrets/stripe", "owner.key", "tls/server.pem"):
            with self.subTest(candidate=candidate):
                self.assertTrue(is_ignored(candidate),
                                f"{candidate} is not ignored")

    def test_the_template_is_not_ignored_because_it_is_documentation(self):
        """Guards the test above: ignoring *.env must not swallow the example
        file the owner is meant to read."""
        self.assertFalse(is_ignored("deploy/solvent.env.example"))

    def test_the_template_is_tracked_and_carries_no_value(self):
        template = REPO / "deploy" / "solvent.env.example"
        self.assertTrue(template.exists())
        for line in template.read_text().splitlines():
            if line.startswith(("SOLVENT_OWNER_KEY", "SOLVENT_STRIPE")):
                with self.subTest(line=line):
                    self.assertTrue(line.endswith("="),
                                    "the template carries a value")

    def test_no_real_secret_file_exists_in_the_repository(self):
        for name in ("deploy/solvent.env", ".env", "solvent.env"):
            with self.subTest(name=name):
                self.assertFalse((REPO / name).exists())


class TheDeploymentInstructionsAgreeWithThemselves(unittest.TestCase):
    """A permission the documentation gets wrong is a deployment that starts and
    reports every credential as missing."""

    def test_the_installer_and_the_unit_name_the_same_file(self):
        unit = (REPO / "deploy" / "solvent.service").read_text()
        installer = (REPO / "deploy" / "install.sh").read_text()
        self.assertIn("/etc/solvent/solvent.env", unit)
        self.assertIn("solvent.env", installer)

    def test_nothing_tells_the_owner_to_make_it_root_only(self):
        """0600 root:solvent is root-only: the service user could not read it.
        Two files said exactly that."""
        for name in ("solvent.service", "solvent.env.example", "install.sh"):
            text = (REPO / "deploy" / name).read_text()
            for line in text.splitlines():
                if "0600" in line and "solvent.env" in line.lower():
                    self.fail(f"{name}: {line.strip()}")

    def test_the_installer_uses_a_mode_the_service_user_can_read(self):
        installer = (REPO / "deploy" / "install.sh").read_text()
        self.assertIn("-m 0640", installer)

    def test_the_backup_script_excludes_the_secret_file(self):
        backup = (REPO / "deploy" / "backup.sh").read_text()
        self.assertIn("SECRETS ARE DELIBERATELY NOT INCLUDED", backup)
        self.assertNotIn("/etc/solvent/solvent.env\"", backup)

    def test_the_environment_variable_names_match_the_code(self):
        """If a variable is renamed in code the template goes stale silently,
        and the owner fills in a name nothing reads."""
        template = (REPO / "deploy" / "solvent.env.example").read_text()
        self.assertIn(KEY_ENV, template)
        self.assertIn(STRIPE_SECRET_ENV, template)
