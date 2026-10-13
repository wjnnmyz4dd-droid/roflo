"""The control centre's status vocabulary, and the two rules behind it.

This surface spent its life reporting a halted business as NORMAL and a Solvent
that had left simulation as SIMULATED. The cause was a wrong query, fixed
elsewhere; what let it go unnoticed for so long was that three of the four
status vocabularies in use had no way to say *I cannot tell*, so a page that
could not establish a fact reported the reassuring half of a boolean.

Two rules are therefore enforced in code rather than remembered:

1. ``VERIFIED HEALTHY`` cannot be constructed without evidence.
2. Evidence older than the thing it describes is not evidence; it is
   ``UNKNOWN``.

Everything else here is the translation table between the diagnostics
authority's states and the five the owner reads, plus the one fact that
authority structurally cannot report: whether the runtime that writes its
reports is running at all.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
import unittest

from solvent import diagnostics as diag
from solvent.web import board, status as st


class TheVocabularyRefusesTwoThings(unittest.TestCase):

    def test_verified_healthy_needs_evidence(self):
        with self.assertRaises(ValueError) as caught:
            st.Status(st.VERIFIED_HEALTHY, "everything is fine")
        self.assertIn("needs evidence", str(caught.exception))

    def test_verified_healthy_needs_evidence_that_is_not_whitespace(self):
        with self.assertRaises(ValueError):
            st.Status(st.VERIFIED_HEALTHY, "fine", evidence="   ")

    def test_a_sixth_status_word_is_refused(self):
        """Five vocabularies is how this started. A sixth word is the next
        one, and it arrives one well-meaning page at a time."""
        for invented in ("FINE", "OK", "HEALTHY", "DONE", "ACTION", ""):
            with self.subTest(word=invented):
                with self.assertRaises(ValueError):
                    st.Status(invented, "x")

    def test_the_other_four_states_need_no_evidence(self):
        """Only the green one makes a claim. Saying "I cannot tell" is not a
        claim that needs backing, and requiring evidence for it would push
        pages back towards reporting the reassuring answer."""
        for state in (st.OWNER_ACTION_REQUIRED, st.UNKNOWN, st.UNSAFE,
                      st.BLOCKED):
            with self.subTest(state=state):
                self.assertEqual(st.Status(state, "x").state, state)

    def test_unknown_is_not_rendered_as_neutral(self):
        """A fact nobody could establish is a warning. Rendering it grey made
        it read as "nothing to see here", which is the opposite of true."""
        self.assertEqual(st.TONE[st.UNKNOWN], "warn")

    def test_every_state_has_a_tone_and_a_place_in_the_order(self):
        self.assertEqual(set(st.TONE), set(st.ORDER))

    def test_the_order_puts_the_dangerous_states_first(self):
        self.assertEqual(st.ORDER[0], st.UNSAFE)
        self.assertEqual(st.ORDER[-1], st.VERIFIED_HEALTHY)


class StaleEvidenceIsNotEvidence(unittest.TestCase):

    def test_fresh_evidence_reads_healthy(self):
        self.assertEqual(
            st.fresh("x", "y", age_seconds=10, stale_after=60).state,
            st.VERIFIED_HEALTHY)

    def test_evidence_past_its_window_reads_unknown(self):
        answer = st.fresh("the runtime reported", "heartbeat",
                          age_seconds=600, stale_after=60)
        self.assertEqual(answer.state, st.UNKNOWN)
        self.assertIn("too old", answer.detail)

    def test_no_evidence_at_all_reads_unknown_rather_than_failed(self):
        """Nothing was established. That is a different sentence from
        "something is wrong", and conflating them trains an owner to ignore
        the page that cries wolf."""
        answer = st.fresh("x", "y", age_seconds=None, stale_after=60)
        self.assertEqual(answer.state, st.UNKNOWN)

    def test_a_stale_answer_does_not_keep_the_previous_verdict(self):
        answer = st.fresh("x", "y", age_seconds=10 ** 6, stale_after=60)
        self.assertNotEqual(answer.state, st.VERIFIED_HEALTHY)
        self.assertEqual(answer.evidence, "",
                         "a stale status carried evidence forward")


class TheWorstStateWins(unittest.TestCase):

    def test_one_unsafe_row_decides_the_summary(self):
        """A page with one UNSAFE row and nineteen healthy ones is an unsafe
        page. An average would be the most misleading sentence on it."""
        rows = [st.healthy("a", "e")] * 19 + [st.unsafe("b")]
        self.assertEqual(st.worst(rows), st.UNSAFE)

    def test_no_rows_at_all_is_unknown_not_healthy(self):
        self.assertEqual(st.worst([]), st.UNKNOWN)

    def test_a_board_summarises_to_its_worst_member(self):
        b = st.Board()
        b.add("a", st.healthy("fine", "evidence"))
        b.add("b", st.unknown("cannot tell"))
        self.assertEqual(b.summary, st.UNKNOWN)
        self.assertEqual([n for n, _ in b.needing_attention()], ["b"])

    def test_a_board_sorts_worst_first(self):
        b = st.Board()
        b.add("healthy", st.healthy("fine", "evidence"))
        b.add("unsafe", st.unsafe("bad"))
        b.add("unknown", st.unknown("cannot tell"))
        self.assertEqual([n for n, _ in b.sorted_worst_first()],
                         ["unsafe", "unknown", "healthy"])


class TheTranslationTableIsComplete(unittest.TestCase):

    def test_every_diagnostics_state_is_mapped(self):
        """An unmapped state would fall through to a default, and the only
        safe default is UNKNOWN -- but silently defaulting is how a surface
        comes to report things it cannot interpret."""
        self.assertEqual(set(diag.STATES), set(board.FROM_DIAGNOSTICS))

    def test_an_unrecognised_state_becomes_unknown_not_healthy(self):
        answer = board.translate({"state": "MARVELLOUS", "detail": "d"})
        self.assertEqual(answer.state, st.UNKNOWN)

    def test_a_healthy_finding_carries_the_authority_as_its_evidence(self):
        answer = board.translate({"state": diag.HEALTHY, "detail": "d"})
        self.assertEqual(answer.state, st.VERIFIED_HEALTHY)
        self.assertIn("diagnostics authority", answer.evidence)

    def test_a_failed_check_is_blocked_rather_than_unsafe(self):
        """A check that failed has stopped something. That is a different
        thing from something dangerous being permitted, and merging them
        would make UNSAFE mean nothing in particular."""
        self.assertEqual(
            board.translate({"state": diag.FAILED, "detail": "d"}).state,
            st.BLOCKED)

    def test_an_unsafe_check_stays_unsafe(self):
        self.assertEqual(
            board.translate({"state": diag.UNSAFE, "detail": "d"}).state,
            st.UNSAFE)

    def test_a_finding_with_no_detail_still_says_something(self):
        answer = board.translate({"state": diag.UNKNOWN})
        self.assertTrue(answer.detail.strip())


class LivenessComesFromTheHeartbeat(unittest.TestCase):
    """The diagnostics authority runs inside the runtime, so it cannot report
    the runtime's absence. A process cannot notice its own absence, and
    nothing looks exactly like quiet-because-everything-is-fine.
    """

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.beat = self.dir / "heartbeat"

    def test_a_fresh_heartbeat_is_verified_healthy(self):
        self.beat.write_text("x")
        answer = board.runtime_liveness(str(self.beat))
        self.assertEqual(answer.state, st.VERIFIED_HEALTHY)
        self.assertIn(str(self.beat), answer.evidence)

    def test_a_stale_heartbeat_is_unknown_not_dead(self):
        """Stopped and wedged are different problems, and the file's age
        cannot tell them apart. Claiming either would be a guess."""
        self.beat.write_text("x")
        old = time.time() - board.HEARTBEAT_STALE_SECONDS - 60
        os.utime(self.beat, (old, old))
        answer = board.runtime_liveness(str(self.beat))
        self.assertEqual(answer.state, st.UNKNOWN)
        self.assertIn("wedged", answer.detail)

    def test_a_missing_heartbeat_is_unknown(self):
        answer = board.runtime_liveness(str(self.beat))
        self.assertEqual(answer.state, st.UNKNOWN)

    def test_no_configured_path_is_unknown_and_names_the_repair(self):
        answer = board.runtime_liveness("")
        self.assertEqual(answer.state, st.UNKNOWN)
        self.assertIn("repair", answer.action.lower())

    def test_a_heartbeat_this_process_may_not_read_is_unknown(self):
        """The control centre runs as a restricted identity now. A refused
        read is a fact about this page, not about the runtime, and it must not
        be reported as the runtime being down.

        The refusal is injected rather than created with chmod, so this runs
        as root too. It previously skipped there, and a test that only runs
        on one kind of machine is a test nobody notices breaking: as root it
        never executed, and the behaviour it describes was in fact wrong --
        `stat()` needs no read permission, so an unreadable heartbeat
        reported VERIFIED HEALTHY.
        """
        self.beat.write_text("x")
        target = str(self.beat)
        real_open = pathlib.Path.open

        def refusing_open(self_path, *args, **kwargs):
            if str(self_path) == target:
                raise PermissionError(13, "Permission denied")
            return real_open(self_path, *args, **kwargs)

        pathlib.Path.open = refusing_open
        self.addCleanup(setattr, pathlib.Path, "open", real_open)
        try:
            answer = board.runtime_liveness(target)
        finally:
            pathlib.Path.open = real_open
        self.assertEqual(answer.state, st.UNKNOWN)
        self.assertIn("permissions", answer.action.lower())

    def test_a_readable_heartbeat_is_still_read_as_fresh(self):
        """Guards the above: a fix that refused everything would pass it."""
        self.beat.write_text("x")
        answer = board.runtime_liveness(str(self.beat))
        self.assertEqual(answer.state, st.VERIFIED_HEALTHY)

    def test_liveness_consults_nothing_but_the_file(self):
        """What this replaces: the page reported the runtime as running
        because the audit log held a "runtime started" event. A process that
        started and then died wrote one of those, so liveness must not be
        answerable from the database at all -- and this function is given no
        way to reach it.
        """
        import inspect

        taken = set(inspect.signature(board.runtime_liveness).parameters)
        self.assertEqual(taken, {"heartbeat_path", "now"},
                         "liveness was handed something it could ask instead "
                         "of looking at the file")

    def test_a_database_full_of_startup_events_does_not_make_it_live(self):
        """The behavioural half: a board built over a projection that records
        a healthy, recently-started runtime still reports UNKNOWN when no
        heartbeat has been written."""
        class Read:
            def posture(self):
                return {"simulation_only": True, "allowlist": [],
                        "operating_mode": "NORMAL", "halted": False}

            def audit_chain_intact(self):
                return True, "recomputed"

            def latest_diagnostics(self):
                return {"present": True, "stale": False, "age_seconds": 1.0,
                        "ran_at": "now", "worst": diag.HEALTHY,
                        "findings": [{"name": "jobs", "state": diag.HEALTHY,
                                      "detail": "runtime started"}]}

        built = board.build(Read(), heartbeat_path=str(self.beat))
        self.assertEqual(dict(built.rows)["Runtime"].state, st.UNKNOWN)


class ThePostureFactsReadHonestly(unittest.TestCase):

    def test_halt_is_blocked_rather_than_a_fault(self):
        """It is the owner's switch in the position they left it. Reporting it
        as a failure trains them to ignore it."""
        answer = board.operating_mode({"operating_mode": "HALT"})
        self.assertEqual(answer.state, st.BLOCKED)
        self.assertIn("HALT", answer.detail)

    def test_normal_is_verified_healthy_with_evidence(self):
        answer = board.operating_mode({"operating_mode": "NORMAL"})
        self.assertEqual(answer.state, st.VERIFIED_HEALTHY)
        self.assertIn("policy document", answer.evidence)

    def test_a_mode_this_page_does_not_know_is_unknown(self):
        self.assertEqual(
            board.operating_mode({"operating_mode": "PAUSED"}).state,
            st.UNKNOWN)

    def test_a_missing_mode_is_unknown(self):
        self.assertEqual(board.operating_mode({}).state, st.UNKNOWN)

    def test_simulation_on_is_healthy_and_not_an_action_item(self):
        """It is the safe state and the one this deployment is meant to be in.
        Nagging about it would teach the owner to leave it."""
        answer = board.execution_mode({"simulation_only": True})
        self.assertEqual(answer.state, st.VERIFIED_HEALTHY)

    def test_real_execution_with_no_allowlist_is_unsafe(self):
        answer = board.execution_mode({"simulation_only": False,
                                       "allowlist": []})
        self.assertEqual(answer.state, st.UNSAFE)

    def test_real_execution_with_an_allowlist_is_reported_not_hidden(self):
        answer = board.execution_mode({
            "simulation_only": False,
            "allowlist": [{"host": "api.example.com"}]})
        self.assertEqual(answer.state, st.OWNER_ACTION_REQUIRED)
        self.assertIn("real execution is permitted", answer.detail)

    def test_an_absent_posture_defaults_to_simulation(self):
        """The safe direction. A projection that could not read the policy
        must not report that real effects are permitted."""
        self.assertEqual(board.execution_mode({}).state, st.VERIFIED_HEALTHY)

    def test_a_broken_audit_chain_is_the_most_serious_thing_on_the_page(self):
        answer = board.audit_chain(False, "record 41 does not hash")
        self.assertEqual(answer.state, st.UNSAFE)
        self.assertIn("41", answer.detail)

    def test_an_intact_chain_says_what_it_rests_on(self):
        answer = board.audit_chain(True, "recomputed 412 records")
        self.assertEqual(answer.state, st.VERIFIED_HEALTHY)
        self.assertIn("412", answer.evidence)


class AStaleReportDoesNotFillTheBoard(unittest.TestCase):

    def test_a_stale_report_contributes_no_findings(self):
        """A stale report describes a moment that has passed. Listing its rows
        as current state is exactly the reassuring-and-wrong failure the whole
        diagnostics subsystem exists to prevent."""
        class Read:
            def posture(self):
                return {"simulation_only": True, "allowlist": [],
                        "operating_mode": "NORMAL", "halted": False}

            def audit_chain_intact(self):
                return True, "recomputed"

            def latest_diagnostics(self):
                return {"present": True, "stale": True,
                        "age_seconds": 10 ** 5, "ran_at": "then",
                        "worst": diag.UNKNOWN,
                        "findings": [{"name": "database",
                                      "state": diag.HEALTHY,
                                      "detail": "fine"}]}

        built = board.build(Read())
        names = [name for name, _ in built.rows]
        self.assertNotIn("Database", names)
        self.assertIn("Diagnostics", names)
        self.assertEqual(dict(built.rows)["Diagnostics"].state, st.UNKNOWN)

    def test_a_fresh_report_does_contribute_its_findings(self):
        class Read:
            def posture(self):
                return {"simulation_only": True, "allowlist": [],
                        "operating_mode": "NORMAL", "halted": False}

            def audit_chain_intact(self):
                return True, "recomputed"

            def latest_diagnostics(self):
                return {"present": True, "stale": False, "age_seconds": 5.0,
                        "ran_at": "now", "worst": diag.HEALTHY,
                        "findings": [{"name": "audit_chain",
                                      "state": diag.UNSAFE,
                                      "detail": "does not verify"}]}

        built = board.build(Read())
        self.assertIn("Audit chain", [name for name, _ in built.rows])
        self.assertEqual(built.summary, st.UNSAFE)

    def test_no_report_at_all_is_unknown_and_says_why(self):
        class Read:
            def posture(self):
                return {"simulation_only": True, "allowlist": [],
                        "operating_mode": "NORMAL", "halted": False}

            def audit_chain_intact(self):
                return True, "recomputed"

            def latest_diagnostics(self):
                return {"present": False, "stale": True, "age_seconds": None,
                        "ran_at": "", "worst": diag.UNKNOWN, "findings": []}

        built = board.build(Read())
        entry = dict(built.rows)["Diagnostics"]
        self.assertEqual(entry.state, st.UNKNOWN)
        self.assertIn("runtime", entry.action.lower())


if __name__ == "__main__":
    unittest.main()
