"""The other end of the control surface: the owner answering what it asked for.

The website can queue a consequential request and cannot complete one, because
it does not hold the key that mints an owner approval. That is the property
that keeps a compromised website from being a compromised business, and on its
own it leaves the owner with a queue and no way to empty it. This is the path
that empties it, and these are the tests that it did not become a bypass in the
process.

The sharpest one here is :class:`OneApprovalAuthorisesOneIntent`. The runtime
used to take an approval for the *run* and hand the same one to every intent it
found in the spool, so an owner who reviewed a single action authorised
everything else queued at that moment — including anything written a second
earlier by whoever had the website. Reviewing one thing and authorising another
is a confused deputy, and it was sitting in the one place the whole design
depends on.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from solvent import harness
from solvent.cli import main as cli_main
from solvent.harness import OWNER, Solvent, consume_owner_intents, intent_subject
from solvent.owner import KEY_ENV, SignedApproval
from solvent.types import ActionClass, OperatingMode
from solvent.web.intents import (CONSEQUENTIAL, INTENTS, IntentWriter, SAFE,
                                 TERMINAL_COMMAND, TERMINAL_ONLY, classify)

#: A test key. Not a credential and not shaped like one: it is the literal
#: word, repeated to clear the length floor, so nobody can mistake a grep hit
#: in this file for something that was ever real.
TEST_KEY = "test-key-not-a-real-credential-" * 2


class Spooled(unittest.TestCase):
    """A Solvent with an owner key and an intent spool on disk."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)
        self.spool = str(self.root / "owner-intents")
        self.db = str(self.root / "solvent.db")

        previous = os.environ.get(KEY_ENV)
        os.environ[KEY_ENV] = TEST_KEY
        self.addCleanup(lambda: (os.environ.__setitem__(KEY_ENV, previous)
                                 if previous is not None
                                 else os.environ.pop(KEY_ENV, None)))
        self.s = Solvent(self.db)
        self.writer = IntentWriter(self.spool)

    def queue(self, verb, *, why="because the owner said so", subject="",
              params=None):
        return self.writer.write(verb=verb, requested_by="owner:control-centre",
                                 why=why, subject=subject, params=params)

    def approval_for(self, intent_id, **kwargs):
        return self.s.owner.issue(
            owner_identity=OWNER, subject=intent_subject(intent_id),
            action_class=ActionClass.C5_AUTHORITY_CHANGE, **kwargs)

    def apply(self, approval=None):
        return consume_owner_intents(self.s, spool=self.spool, approval=approval)

    def outcome(self, results, intent_id):
        for record in results:
            if record["intent"].startswith(intent_id):
                return record
        self.fail(f"{intent_id} was not processed: {results}")


class TheClosedLoop(Spooled):
    """A queued consequential action can actually be completed."""

    def test_a_safe_intent_needs_no_approval(self):
        self.queue("halt", why="stopping for the night")
        results = self.apply()
        self.assertEqual(results[0]["outcome"], "HALT engaged")
        self.assertIs(self.s.policy.operating_mode, OperatingMode.HALT)

    def test_a_consequential_intent_is_left_queued_with_no_approval(self):
        """Not refused — not yet answered. Refusing would discard the ask."""
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        record = self.queue("resume", why="the incident is closed")
        result = self.outcome(self.apply(), record["id"])
        self.assertEqual(result["outcome"], harness.QUEUED)
        self.assertIs(self.s.policy.operating_mode, OperatingMode.HALT)
        self.assertIn(record["id"],
                      [r["id"] for r in IntentWriter(self.spool).pending()],
                      "an unapproved request was swept out of the queue")

    def test_a_consequential_intent_completes_with_its_own_approval(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        record = self.queue("resume", why="the incident is closed")
        results = self.apply(self.approval_for(record["id"]))
        self.assertEqual(self.outcome(results, record["id"])["outcome"],
                         "operating mode NORMAL")
        self.assertIs(self.s.policy.operating_mode, OperatingMode.NORMAL)

    def test_the_approval_is_burned_once_the_action_happened(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        record = self.queue("resume", why="done")
        approval = self.approval_for(record["id"])
        self.assertTrue(any(r["nonce"] == approval.nonce
                            for r in self.s.owner.outstanding()))
        self.apply(approval)
        self.assertFalse(any(r["nonce"] == approval.nonce
                             for r in self.s.owner.outstanding()))

    def test_a_refused_intent_does_not_burn_the_approval(self):
        """A failure must not cost the owner the approval they just minted."""
        record = self.queue("retire_skill_version", why="withdrawing it",
                            params={"skill": "nothing-like-this",
                                    "version": "9.9"})
        approval = self.approval_for(record["id"])
        result = self.outcome(self.apply(approval), record["id"])
        self.assertEqual(result["outcome"], "REFUSED")
        self.assertTrue(any(r["nonce"] == approval.nonce
                            for r in self.s.owner.outstanding()),
                        "a failed action spent the owner's approval")

    def test_what_was_acted_on_reaches_the_audit_log(self):
        """Processed means acted on. An intent still waiting is not an event."""
        self.queue("halt", why="safe one")
        waiting = self.queue("resume", why="consequential one")
        self.apply()
        events = [e for e in self.s.audit.events()
                  if e["event"] == "owner.intent_processed"]
        self.assertEqual([e["decision"] for e in events], ["halt"])
        self.assertEqual(waiting["intent_class"], CONSEQUENTIAL)

    def test_a_refusal_reaches_the_audit_log_with_its_verb(self):
        """A refusal is what an intrusion looks like afterwards, so it is
        recorded with enough to tell what was attempted."""
        record = self.queue("record_contracting", why="from the website")
        self.apply(self.approval_for(record["id"]))
        events = [e for e in self.s.audit.events()
                  if e["event"] == "owner.intent_processed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["decision"], "record_contracting")
        self.assertIn("REFUSED", events[0]["result"])


class OneApprovalAuthorisesOneIntent(Spooled):
    """The confused-deputy hole, and the binding that closed it."""

    def test_an_approval_for_one_intent_does_not_carry_another(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        mine = self.queue("resume", why="the incident I reviewed is closed")
        theirs = self.queue("promote_capability", subject="proj_whatever",
                            why="queued by somebody else a second earlier")

        results = self.apply(self.approval_for(mine["id"]))
        self.assertEqual(self.outcome(results, mine["id"])["outcome"],
                         "operating mode NORMAL")
        other = self.outcome(results, theirs["id"])
        self.assertEqual(other["outcome"], harness.QUEUED)
        self.assertIn(theirs["id"],
                      [r["id"] for r in IntentWriter(self.spool).pending()],
                      "approving one intent discarded the owner's other one")

    def test_the_subject_cannot_be_repointed_without_breaking_the_signature(self):
        """``subject`` is inside the signed payload, which is why this works."""
        first = self.queue("resume", why="one")
        second = self.queue("resume", why="two")
        approval = self.approval_for(first["id"])
        forged = SignedApproval(
            **{**{f: getattr(approval, f) for f in approval.__dataclass_fields__},
               "subject": intent_subject(second["id"])})
        results = self.apply(forged)
        self.assertIn("signature", self.outcome(results, second["id"])["why"])
        self.assertEqual(self.outcome(results, first["id"])["outcome"],
                         harness.QUEUED)

    def test_approving_each_one_separately_is_what_it_takes(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        self.s.policy.approve_payment_rail(
            owner_identity=OWNER, rail="a hosted checkout",
            verification_signal="a settled-payment webhook",
            reason="chosen so the ladder has a bottom rung")
        first = self.queue("resume", why="clearing the halt")
        second = self.queue("advance_payment_rail", why="engineering is done",
                            params={"state": "ENGINEERING_READY"})
        self.apply(self.approval_for(first["id"]))
        self.assertIs(self.s.policy.operating_mode, OperatingMode.NORMAL)
        results = self.apply(self.approval_for(second["id"]))
        self.assertIn("ENGINEERING_READY",
                      self.outcome(results, second["id"])["outcome"])


class ApprovalsAreChecked(Spooled):
    """Everything the Owner Channel refuses, refused here too."""

    def test_a_forged_signature_is_refused(self):
        record = self.queue("resume", why="please")
        approval = self.approval_for(record["id"])
        forged = SignedApproval(
            **{**{f: getattr(approval, f) for f in approval.__dataclass_fields__},
               "signature": "0" * 64})
        result = self.outcome(self.apply(forged), record["id"])
        self.assertEqual(result["outcome"], "REFUSED")
        self.assertIn("signature", result["why"])

    def test_an_expired_approval_is_refused(self):
        record = self.queue("resume", why="please")
        approval = self.approval_for(record["id"], ttl_minutes=1)
        stale = SignedApproval(
            **{**{f: getattr(approval, f) for f in approval.__dataclass_fields__},
               "expires_at": (datetime.now(timezone.utc)
                              - timedelta(minutes=5)).isoformat()})
        result = self.outcome(self.apply(stale), record["id"])
        self.assertEqual(result["outcome"], "REFUSED")

    def test_an_approval_cannot_be_replayed(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        first = self.queue("resume", why="once")
        approval = self.approval_for(first["id"])
        self.apply(approval)
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "again")
        # Same id, so the subject still matches — only the nonce is spent.
        self.writer.write(verb="resume", requested_by="owner:control-centre",
                          why="twice", subject="", params=None)
        replayed = pathlib.Path(self.spool)
        for path in replayed.glob("*.intent"):
            body = json.loads(path.read_text())
            body["id"] = first["id"]
            path.write_text(json.dumps(body))
            os.replace(path, replayed / f"{first['id']}.intent")
        result = self.outcome(self.apply(approval), first["id"])
        self.assertEqual(result["outcome"], "REFUSED")
        self.assertIn("already used", result["why"])
        self.assertIs(self.s.policy.operating_mode, OperatingMode.HALT)

    def test_the_owner_identity_comes_from_the_approval_not_the_spool(self):
        """A name typed into a spool file is not an identity.

        The second registered owner is what makes this test mean anything. An
        earlier version claimed an unregistered name, and passed — but it
        passed because Policy refuses an unregistered owner, which is a
        different control doing the work. Mutation testing caught it: taking
        the identity from the spool file survived, because the assertion was
        about the outcome rather than about which control produced it.

        With two real owners, the claimed name is one Policy would accept, so
        the only thing standing between the spool file and an action recorded
        under somebody else's name is where this reads the identity from.
        """
        second = "owner:second-signatory"
        self.s.policy.amend(
            {"governance": {"owner_identities": [OWNER, second]}},
            OWNER, "a second owner, so the claimed name is a real one")
        self.assertTrue(self.s.policy.is_owner(second))

        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        record = self.queue("resume", why="done")
        path = pathlib.Path(self.spool) / f"{record['id']}.intent"
        body = json.loads(path.read_text())
        body["owner_identity"] = second
        path.write_text(json.dumps(body))

        # The approval is the first owner's. The spool file claims the second.
        self.apply(self.approval_for(record["id"]))
        self.assertIs(self.s.policy.operating_mode, OperatingMode.NORMAL,
                      "the action did not happen, so this proves nothing")
        claimed = [e for e in self.s.audit.events()
                   if e["initiator"] == second]
        self.assertEqual(claimed, [],
                         "an identity claimed in a spool file was acted under, "
                         "and the audit log now names the wrong owner")


class WhatAnApprovalStillCannotDo(Spooled):
    """Some things are not completed by approving a file, and say so."""

    def test_the_terminal_only_verbs_are_refused_even_with_an_approval(self):
        for verb in sorted(TERMINAL_ONLY):
            with self.subTest(verb=verb):
                record = self.queue(verb, why="trying it from the website")
                result = self.outcome(
                    self.apply(self.approval_for(record["id"])), record["id"])
                self.assertEqual(result["outcome"], "REFUSED")
                self.assertIn(TERMINAL_COMMAND[verb], result["why"])

    def test_going_live_is_not_a_button(self):
        """The one change that turns a simulation into a real business."""
        self.assertIn("enable_real_execution", TERMINAL_ONLY)
        record = self.queue("enable_real_execution", why="go live")
        self.apply(self.approval_for(record["id"]))
        self.assertTrue(self.s.policy.get("simulation_only", default=True))

    def test_an_unknown_verb_is_refused_even_with_an_approval(self):
        path = pathlib.Path(self.spool)
        path.mkdir(parents=True, exist_ok=True)
        (path / "int_forged.intent").write_text(json.dumps(
            {"id": "int_forged", "verb": "grant_everything", "why": "hello"}))
        result = self.outcome(self.apply(self.approval_for("int_forged")),
                              "int_forged")
        self.assertEqual(result["outcome"], "REFUSED")

    def test_every_consequential_verb_either_runs_or_says_why_not(self):
        """No verb on the list may silently do nothing and report success."""
        for verb, (kind, _) in INTENTS.items():
            if kind != CONSEQUENTIAL:
                continue
            with self.subTest(verb=verb):
                record = self.queue(verb, why="exercising the verb",
                                    subject="subject", params={})
                result = self.outcome(
                    self.apply(self.approval_for(record["id"])), record["id"])
                self.assertTrue(result["outcome"])
                if result["outcome"] != "REFUSED":
                    continue
                self.assertNotIn("nothing here carries it out", result["why"],
                                 f"{verb} is offered and unimplemented")


class TheOwnerCommandLine(Spooled):
    """What the owner actually types, end to end."""

    def run_cli(self, *argv):
        return cli_main([*argv, "--spool", self.spool, "--db", self.db])

    def test_listing_an_empty_spool_says_so(self):
        self.assertEqual(self.run_cli("intents", "list"), 0)

    def test_listing_names_the_command_each_intent_needs(self):
        self.queue("halt", why="safe")
        self.queue("resume", why="consequential")
        self.queue("record_contracting", why="terminal only")
        self.assertEqual(self.run_cli("intents", "list"), 0)

    def test_run_applies_the_safe_ones_and_leaves_the_rest_queued(self):
        self.queue("halt", why="stop")
        kept = self.queue("resume", why="start")
        self.assertEqual(self.run_cli("intents", "run"), 0)
        self.assertIs(self.s.policy.operating_mode, OperatingMode.HALT)
        self.assertEqual([r["id"] for r in IntentWriter(self.spool).pending()],
                         [kept["id"]],
                         "running the safe intents should not clear the rest")

    def test_approve_completes_exactly_the_named_intent(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        mine = self.queue("resume", why="reviewed and fine")
        theirs = self.queue("promote_capability", subject="proj_x", why="not mine")
        self.assertEqual(
            self.run_cli("intents", "approve", mine["id"], "--reason", "reviewed"),
            0)
        self.assertIs(Solvent(self.db).policy.operating_mode, OperatingMode.NORMAL)
        self.assertEqual([r["id"] for r in IntentWriter(self.spool).pending()],
                         [theirs["id"]],
                         "approving one intent disposed of the other")

    def test_approving_an_unknown_intent_fails_rather_than_guessing(self):
        self.assertEqual(
            self.run_cli("intents", "approve", "int_nope", "--reason", "x"), 2)

    def test_approving_a_terminal_only_verb_points_at_the_command(self):
        record = self.queue("record_contracting", why="from the website")
        self.assertEqual(
            self.run_cli("intents", "approve", record["id"], "--reason", "x"), 2)
        self.assertTrue(IntentWriter(self.spool).pending(),
                        "a refusal to approve should leave the intent queued")

    def test_reject_records_the_refusal_and_clears_the_queue(self):
        record = self.queue("resume", why="I do not want this")
        self.assertEqual(
            self.run_cli("intents", "reject", record["id"], "--reason", "no"), 0)
        self.assertEqual(IntentWriter(self.spool).pending(), [])
        after = Solvent(self.db)
        self.assertTrue([e for e in after.audit.events()
                         if e["event"] == "owner.intent_rejected"])

    def test_reject_does_not_carry_the_action_out(self):
        self.s.policy.set_operating_mode(OperatingMode.HALT, OWNER, "test")
        record = self.queue("resume", why="no thanks")
        self.run_cli("intents", "reject", record["id"], "--reason", "no")
        self.assertIs(Solvent(self.db).policy.operating_mode, OperatingMode.HALT)


class TheWebProcessStillCannotDoThis(unittest.TestCase):
    """The loop closed at the terminal, and nowhere else."""

    def test_the_web_package_does_not_import_the_owner_channel(self):
        import solvent.web.app
        import solvent.web.intents
        import solvent.web.readmodel
        import solvent.web.server
        for module in (solvent.web.app, solvent.web.intents,
                       solvent.web.readmodel, solvent.web.server):
            source = pathlib.Path(module.__file__).read_text()
            self.assertNotIn("OwnerChannel", source)
            self.assertNotIn("SOLVENT_OWNER_KEY", source)

    def test_the_intent_writer_cannot_mint_an_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer = IntentWriter(str(pathlib.Path(tmp) / "spool"))
            record = writer.write(verb="resume", requested_by="web",
                                  why="trying", subject="", params=None)
        self.assertNotIn("approval", record)
        self.assertTrue(record["requires_owner_approval"])
        self.assertEqual(classify("resume"), CONSEQUENTIAL)

    def test_the_safe_verbs_are_the_ones_whose_worst_case_is_doing_less(self):
        for verb, (kind, _) in INTENTS.items():
            if kind is SAFE:
                self.assertNotIn(verb, TERMINAL_ONLY)
