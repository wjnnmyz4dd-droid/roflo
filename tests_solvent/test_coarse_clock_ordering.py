"""Two rows written in the same clock tick must still have an order.

A real Windows runner on Python 3.11 failed twenty-three tests in
``test_skills_lab`` and ``test_resilience`` while the same commit was
green on 3.13 and on Linux. The cause is one clause, repeated in ten
places:

    ORDER BY ts, id

``ts`` is ``datetime.now(timezone.utc).isoformat()`` and ``id`` is
``uuid4().hex[:16]``. When two rows share a timestamp the tiebreaker
is a random number, so "the latest status of this claim" is decided
by a coin toss.

On Linux that almost never happens -- the clock has microsecond
resolution and consecutive inserts differ. On Windows under CPython
3.11 the system clock advances in steps of about 15.6 ms, and CPython
3.13 is the release that switched Windows to
``GetSystemTimePreciseAsFileTime``. So the defect is invisible on
every host this project had measured and routine on the one it had
not: a claim recorded and then verified inside one tick reads as
verified or unverified at random.

It fails closed, so nothing unsafe was permitted. It is still an
evidence authority deciding what the evidence says by chance, and the
same clause orders the flight recorder and the incident timeline,
where losing a tie reorders the account of what happened.

The repair is ``ORDER BY ts, rowid``: SQLite's rowid is assigned on
insert and increases, so rows in one tick keep the order they were
written in.

**These tests do not need Windows.** They freeze the clock, which is
the coarse-clock condition taken to its limit, and run against a real
database.
"""

from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solvent import audit, skillslab as lab                   # noqa: E402
from solvent.harness import Solvent                           # noqa: E402
from tests_solvent.test_skills_lab import allow, rig          # noqa: E402

#: One instant, for every row. A 15.6 ms Windows tick with enough
#: inserts in it looks exactly like this, and a frozen clock is the
#: case the ordering has to survive rather than a case it may assume
#: away.
FROZEN = "2026-10-23T00:00:00+00:00"


class AFrozenClockDoesNotReorderTheEvidence(unittest.TestCase):
    """The skills lab, with every row stamped at the same instant."""

    def setUp(self):
        # Patched on `skillslab`, not on `audit`. Both modules do
        # `from .audit import now`, which binds the function into
        # their own namespace at import -- so patching `audit.now`
        # freezes nothing, and the first version of this file did
        # exactly that and passed against the defect it was written
        # to catch. The sweep test below is what noticed.
        for module in (lab, "solvent.resilience"):
            target = (module if not isinstance(module, str)
                      else __import__(module, fromlist=["now"]))
            patcher = mock.patch.object(target, "now", lambda: FROZEN)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.s = rig()
        self.addCleanup(self.s.store.close)
        self.covers = frozenset({"collapse_whitespace"})

    def claim_then_verify(self):
        skl = self.s.skillslab
        project = skl.record_need(skill="text-tidy", need="n", observed_on="j")
        skl.check_overlap(project_id=project, target_covers=self.covers)
        allow(self.s, "text-tidy")
        claim = skl.record_claim(project_id=project, claim="m", source="s",
                                 trust=lab.MEASURED)
        skl.verify_claim(claim_id=claim, status=lab.VERIFIED, checked_by="t")
        return project, claim

    def test_the_latest_status_is_the_one_written_last(self):
        _project, claim = self.claim_then_verify()
        self.assertEqual(self.s.skillslab._latest_claim(claim)["status"],
                         lab.VERIFIED)

    def test_a_verified_claim_does_not_block_the_build(self):
        """The twenty-three Windows failures, in one assertion."""
        project, _claim = self.claim_then_verify()
        allowed, why = self.s.skillslab.may_build(project)
        self.assertTrue(allowed, why)

    def test_the_whole_evidence_view_agrees(self):
        project, _claim = self.claim_then_verify()
        statuses = [row["status"]
                    for row in self.s.skillslab.evidence(project)]
        self.assertEqual(statuses, [lab.VERIFIED])

    def test_the_history_keeps_both_rows_in_the_order_written(self):
        """Append-only: the first reading is not replaced, only followed."""
        project, _claim = self.claim_then_verify()
        history = [row["status"]
                   for row in self.s.skillslab.evidence_history(project)]
        self.assertEqual(history, [lab.UNVERIFIED, lab.VERIFIED])

    def test_it_is_not_merely_passing_by_luck_of_the_identifiers(self):
        """Ten runs, each with its own random ids. All ten must agree.

        Before the repair this was a coin toss per run, so ten runs
        failed with probability about 999 in 1000.
        """
        for attempt in range(10):
            with self.subTest(attempt=attempt):
                self.s.store.close()
                self.s = rig()
                _project, claim = self.claim_then_verify()
                self.assertEqual(
                    self.s.skillslab._latest_claim(claim)["status"],
                    lab.VERIFIED)


class NoTimeOrderedQueryIsLeftWithoutATiebreaker(unittest.TestCase):
    """Swept across the whole package, not just where it was found.

    The first sweep looked for ``ORDER BY ts, id`` and repaired ten
    sites. That was too narrow by half: ``ORDER BY ts DESC`` with no
    tiebreaker at all is the same defect and there were forty more of
    them. The one that got through ordered incidents, so
    ``incidents()[0]`` -- "the newest" -- returned whichever of two
    crashes in the same tick SQLite happened to hand back, and the
    test that recorded two crashes and asked whether the second was a
    recurrence was comparing a row with itself.

    A column that is unique per row needs no tiebreaker, so ``rowid``,
    ``seq`` and ``id`` orderings are left alone, as are aggregates and
    orderings by name, where a tie is a presentation detail rather
    than a question of which record is the latest.
    """

    #: Columns whose value can repeat inside one clock tick.
    TIMEISH = ("ts", "updated_at", "issued_at", "ran_at", "last_seen",
               "first_seen", "created_at", "decided_at", "sent_at")

    def offenders(self, text: str) -> list[str]:
        import re

        pattern = re.compile(
            r"ORDER BY ((?:[a-z_]+\.)?(?:" + "|".join(self.TIMEISH) + r"))"
            r"( DESC)?(?![a-z_])")
        found = []
        for match in pattern.finditer(text):
            after = text[match.end():match.end() + 24].lstrip()
            # Qualified too: a join orders by ``a.ts DESC, a.rowid
            # DESC``, and reading only for a bare ``rowid`` reported
            # a correctly tiebroken query as a defect.
            if not re.match(r",\s*(?:[a-z_]+\.)?rowid", after):
                found.append(match.group(0))
        return found

    def test_every_module_in_the_package(self):
        for path in sorted((ROOT / "solvent").rglob("*.py")):
            with self.subTest(path.relative_to(ROOT).as_posix()):
                self.assertEqual(
                    self.offenders(path.read_text(encoding="utf-8")), [],
                    "a time ordering with no tiebreaker: two rows written "
                    "in one clock tick come back in whatever order SQLite "
                    "chooses, and on Windows under CPython 3.11 a tick is "
                    "about 15.6 ms")

    def test_the_check_can_fail(self):
        """It found forty-one; it must not pass on anything."""
        self.assertEqual(self.offenders('"SELECT * FROM x ORDER BY ts DESC"'),
                         ["ORDER BY ts DESC"])
        self.assertEqual(self.offenders('"... ORDER BY updated_at"'),
                         ["ORDER BY updated_at"])

    def test_a_tiebroken_ordering_is_accepted(self):
        for good in ('"ORDER BY ts, rowid"', '"ORDER BY ts DESC, rowid DESC"',
                     '"ORDER BY rowid"', '"ORDER BY seq DESC"',
                     '"ORDER BY name"', '"ORDER BY n DESC"'):
            with self.subTest(good):
                self.assertEqual(self.offenders(good), [])

    def test_a_column_whose_name_merely_starts_the_same_is_not_matched(self):
        """``ts_total`` is not ``ts``."""
        self.assertEqual(self.offenders('"ORDER BY ts_total DESC"'), [])


class NoReaderBreaksTiesWithARandomIdentifier(unittest.TestCase):
    """The clause, swept for across every authority that uses it.

    One of these ten was found by a Windows runner. The others were
    found by looking, and would have behaved the same way on the same
    host -- three of them did, in ``test_resilience``.
    """

    SOURCES = ("solvent/skillslab.py", "solvent/resilience.py",
               "solvent/web/readmodel.py", "solvent/audit.py",
               "solvent/store.py", "solvent/ledger.py")

    def test_no_query_orders_by_a_random_identifier(self):
        for name in self.SOURCES:
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertNotIn("ORDER BY ts, id", text)
                self.assertNotIn("ORDER BY ts DESC, id DESC", text)

    def test_the_identifier_really_is_random(self):
        """The premise, asserted rather than assumed."""
        made = {audit.new_id("x") for _ in range(50)}
        self.assertEqual(len(made), 50)
        self.assertNotEqual(sorted(made), [i for i in made])

    def test_rowid_is_available_on_the_tables_that_now_use_it(self):
        """A WITHOUT ROWID table would make the repair silently wrong."""
        s = Solvent(":memory:")
        self.addCleanup(s.store.close)
        db = s.skillslab._db
        for table in ("skill_evidence", "incident_events", "flight_frames"):
            with self.subTest(table):
                # A WITHOUT ROWID table raises here rather than
                # returning nothing, which is the point: the repair
                # would be silently wrong against one.
                self.assertEqual(
                    list(db.query(f"SELECT rowid FROM {table} LIMIT 1")), [])


if __name__ == "__main__":
    unittest.main()
