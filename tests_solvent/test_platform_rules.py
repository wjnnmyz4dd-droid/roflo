"""Per-platform rules: what state each source is in, and the limits it imposes.

Two things are tested here, and the second is the one that matters
operationally.

**The seven statuses are derived, not stored.** A stored status would be a
second opinion about the same question, and the first time it disagreed with
``access_method`` nobody would know which was right. This project has made that
mistake twice already -- once with diagnostics carrying its own judgement, and
once with two setup pages reading different policy paths. So
:func:`solvent.sourcecatalog.operational_status` is a function of facts somebody
established, and these tests pin the order of its questions, because the order
is what stops a later branch talking its way past a prohibition.

**A rate limit is now a number, not a note.** Every adapter carried a prose
``rate_limit_note`` and nothing enforced it, so "do not bypass rate limits" was
a sentence rather than a control. The poll gate now refuses when too little
time has passed, and refusing is deliberate: sleeping would hold the runtime's
tick for however long the limit says, turning politeness into an availability
problem.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import tempfile
import unittest

from solvent import adapters
from solvent import sourceaccess as access
from solvent import sourcecatalog as cat
from solvent.discovery import Compliance, Readiness
from solvent.harness import OWNER, Solvent


def entry(**kwargs) -> cat.CatalogueEntry:
    base = dict(source_id="x", display_name="X", source_type=cat.DIRECT,
                determination_needed="somebody has to read the terms")
    base.update(kwargs)
    return cat.CatalogueEntry(**base)


class EveryPlatformTheOwnerNamedIsCatalogued(unittest.TestCase):

    #: The platforms the owner asked for, by the name they used.
    REQUIRED = {
        "upwork": "Upwork", "fiverr": "Fiverr", "freelancer": "Freelancer.com",
        "peopleperhour": "PeoplePerHour", "guru": "Guru", "contra": "Contra",
        "sam_gov": "SAM.gov", "grants_gov": "Grants.gov",
        "state_local_procurement": "government procurement portals",
        "rfp_board": "RFP and bid boards",
        "client_inquiry": "authorized company websites / direct inquiry",
        "owner_entered": "email-imported opportunities",
        "file_import": "CSV-imported opportunities",
    }

    def test_each_one_has_an_entry(self):
        have = {e.source_id for e in cat.CATALOGUE}
        missing = {k: v for k, v in self.REQUIRED.items() if k not in have}
        self.assertEqual(missing, {}, f"not catalogued: {missing}")

    def test_no_entry_was_removed_to_make_room(self):
        """Preserving what is registered is a requirement, so a catalogue that
        shrank would be a regression even if every required name were there."""
        self.assertGreaterEqual(len(cat.CATALOGUE), len(self.REQUIRED))

    def test_every_entry_derives_one_of_the_seven_statuses(self):
        for e in cat.CATALOGUE:
            with self.subTest(source=e.source_id):
                status, why = cat.operational_status(e)
                self.assertIn(status, cat.OPERATIONAL_STATUSES)
                self.assertTrue(why.strip(), "a status with no reason given")

    def test_every_marketplace_is_unverified_rather_than_assumed(self):
        """A platform's terms decide whether automated access is permitted,
        they differ per account, and they change. That reading cannot be made
        from inside this repository, and a wrong guess costs the owner their
        account permanently."""
        for e in cat.CATALOGUE:
            if e.source_type != cat.FREELANCE_MARKETPLACE:
                continue
            with self.subTest(source=e.source_id):
                status, _why = cat.operational_status(e)
                self.assertEqual(status, cat.UNVERIFIED)

    def test_no_entry_claims_an_access_method_without_evidence_and_a_date(self):
        """The anti-fabrication rule, enforced rather than trusted."""
        for e in cat.CATALOGUE:
            if e.access_method in (access.UNDETERMINED, access.MANUAL_IMPORT,
                                   access.UNSUPPORTED_AUTOMATION):
                continue
            with self.subTest(source=e.source_id):
                self.assertTrue(e.verified_on,
                                "claims an access method with no date")
                self.assertTrue(e.evidence,
                                "claims an access method with no source")
                datetime.date.fromisoformat(e.verified_on)

    def test_the_manual_paths_are_always_available(self):
        """The first dollar does not require an API. Every entry supports the
        owner pasting in something they found."""
        for e in cat.CATALOGUE:
            with self.subTest(source=e.source_id):
                self.assertTrue(e.manual_import)


class TheStatusAnswersTheQuestionsInOrder(unittest.TestCase):
    """Each test pins one ordering decision. Reordering these breaks a test
    rather than quietly changing what the owner is told."""

    def test_a_platform_restriction_outranks_everything(self):
        """Including a credential being present and an adapter existing. A
        prohibition that a later branch could talk past would not be one."""
        status, why = cat.operational_status(
            entry(access_method=access.UNSUPPORTED_AUTOMATION,
                  verified_on="2026-10-01", evidence="their terms",
                  adapter_implemented=True),
            registered=True, permissions=(access.DISCOVER,),
            credential_present=True, today="2026-10-11")
        self.assertEqual(status, cat.BLOCKED_PLATFORM_RESTRICTION)

    def test_an_undetermined_platform_is_unverified_and_says_what_is_needed(self):
        status, why = cat.operational_status(
            entry(access_method=access.UNDETERMINED,
                  determination_needed="read their terms for this account"))
        self.assertEqual(status, cat.UNVERIFIED)
        self.assertIn("read their terms", why)

    def test_manual_import_is_established_without_a_determination(self):
        """There are no external terms to expire and no credential to hold."""
        status, _why = cat.operational_status(
            entry(access_method=access.MANUAL_IMPORT))
        self.assertEqual(status, cat.ESTABLISHED_MANUAL_IMPORT)

    def test_a_stale_determination_reads_unverified_not_ready(self):
        """Terms change. "Somebody checked at some point" is not a finding,
        and this is checked before the credential so that a stale entry with
        working credentials cannot read as ready."""
        status, why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, verified_on="2020-01-01",
                  evidence="their docs", adapter_implemented=True),
            registered=True, permissions=(access.DISCOVER,),
            credential_present=True, today="2026-10-11")
        self.assertEqual(status, cat.UNVERIFIED)
        self.assertIn("re-reading", why)

    def test_a_determination_with_no_date_is_stale_by_definition(self):
        status, _why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, evidence="their docs",
                  adapter_implemented=True),
            registered=True, permissions=(access.DISCOVER,))
        self.assertEqual(status, cat.UNVERIFIED)

    def test_an_unregistered_source_is_pending_rather_than_blocked(self):
        """Nothing is wrong. A decision is outstanding, and calling that
        BLOCKED would send the owner looking for a fault."""
        status, _why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, verified_on="2026-10-01",
                  evidence="their docs", adapter_implemented=True),
            registered=False, today="2026-10-11")
        self.assertEqual(status, cat.PENDING_OWNER_AUTHORIZATION)

    def test_registering_without_discover_is_pending(self):
        """Registering a source grants the read side only. Discovering is a
        separate grant, which is the distinction the permission model makes."""
        status, why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, verified_on="2026-10-01",
                  evidence="their docs", adapter_implemented=True),
            registered=True, permissions=(access.READ,), today="2026-10-11")
        self.assertEqual(status, cat.PENDING_OWNER_AUTHORIZATION)
        self.assertIn("DISCOVER", why)

    def test_a_missing_credential_blocks_only_once_everything_else_holds(self):
        status, _why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, verified_on="2026-10-01",
                  evidence="their docs", adapter_implemented=True),
            registered=True, permissions=(access.DISCOVER,),
            credential_present=False, today="2026-10-11")
        self.assertEqual(status, cat.BLOCKED_MISSING_CREDENTIALS)

    def test_not_asking_about_a_credential_is_not_the_same_as_absent(self):
        """An entry needing no credential must not read BLOCKED because a
        caller did not look."""
        status, _why = cat.operational_status(
            entry(access_method=access.PUBLIC_FEED, verified_on="2026-10-01",
                  evidence="their docs", adapter_implemented=True),
            registered=True, permissions=(access.DISCOVER,),
            credential_present=None, today="2026-10-11")
        self.assertEqual(status, cat.ESTABLISHED_AUTOMATED)

    def test_an_owner_assisted_platform_is_established_not_automated(self):
        """Not a lesser outcome: it is the difference between a business that
        works and an account that is banned."""
        status, _why = cat.operational_status(
            entry(access_method=access.OWNER_ASSISTED_BROWSER,
                  verified_on="2026-10-01", evidence="their terms"),
            registered=True, permissions=(access.DISCOVER,),
            today="2026-10-11")
        self.assertEqual(status, cat.ESTABLISHED_OWNER_AUTHORIZED)

    def test_a_permitted_platform_with_no_adapter_is_pending(self):
        """A row naming a source is a registration, not code that fetches from
        one -- the same distinction the rehydration path already makes."""
        status, why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, verified_on="2026-10-01",
                  evidence="their docs", adapter_implemented=False),
            registered=True, permissions=(access.DISCOVER,),
            credential_present=True, today="2026-10-11")
        self.assertEqual(status, cat.PENDING_OWNER_AUTHORIZATION)
        self.assertIn("adapter", why)

    def test_everything_established_reads_automated_with_its_evidence(self):
        status, why = cat.operational_status(
            entry(access_method=access.OFFICIAL_API, verified_on="2026-10-01",
                  evidence="open.gsa.gov", adapter_implemented=True),
            registered=True, permissions=(access.DISCOVER,),
            credential_present=True, today="2026-10-11")
        self.assertEqual(status, cat.ESTABLISHED_AUTOMATED)
        self.assertIn("2026-10-01", why)
        self.assertIn("open.gsa.gov", why)


class ARateLimitIsANumberAndItIsEnforced(unittest.TestCase):

    def setUp(self):
        # Restored, not just set. setdefault leaves the variable in the
        # process for every test that runs afterwards, and
        # test_runtime.StartupChecks has one that expects it absent -- so
        # these tests passed alone and broke four others when the whole suite
        # ran. A test that changes the environment has to put it back.
        previous = os.environ.get("SOLVENT_OWNER_KEY")
        os.environ["SOLVENT_OWNER_KEY"] = "test-key-not-a-real-credential-" * 2
        self.addCleanup(
            lambda: os.environ.__setitem__("SOLVENT_OWNER_KEY", previous)
            if previous is not None
            else os.environ.pop("SOLVENT_OWNER_KEY", None))
        self.db = str(pathlib.Path(tempfile.mkdtemp()) / "solvent.db")
        self.s = Solvent(self.db)
        self.addCleanup(self.s.store.close)
        self.source = adapters.GrantsGovSource()
        self.s.discovery.register_source(
            self.source, owner_identity=OWNER,
            readiness=Readiness.PRODUCTION_READY,
            compliance=Compliance.PERMITTED,
            determination="read 2026-09-29; search2 is unauthenticated and "
                          "described as unrestricted")
        self.s.policy.amend(
            {"discovery": {"approved_sources": [self.source.name]}},
            OWNER, "test")

    def state(self) -> dict:
        return self.s.discovery.source_state(self.source.name) or {}

    def stamp(self, value: str) -> None:
        self.s.store.for_authority("discovery").execute(
            "UPDATE work_sources SET last_polled_at = ? WHERE name = ?",
            (value, self.source.name))

    def test_every_fetching_adapter_declares_an_interval(self):
        """An adapter that makes an external call and declares no interval is
        one nothing can hold back."""
        for cls in (adapters.GrantsGovSource, adapters.SamGovOpportunitiesSource):
            with self.subTest(adapter=cls.__name__):
                self.assertGreater(
                    float(getattr(cls, "min_poll_interval_seconds", 0)), 0,
                    f"{cls.__name__} declares no minimum poll interval")

    def test_the_interval_is_recorded_where_an_owner_can_see_it(self):
        self.assertEqual(self.state()["min_poll_interval_seconds"],
                         self.source.min_poll_interval_seconds)

    def test_the_retention_obligation_is_recorded(self):
        """An obligation nobody wrote down is an obligation nobody meets."""
        self.assertTrue(self.state()["retention_note"].strip())

    def test_the_first_poll_is_allowed(self):
        allowed, _why = self.s.discovery._may_poll(self.source)
        self.assertTrue(allowed)

    def test_a_second_poll_straight_away_is_refused(self):
        self.s.discovery._record_poll(self.source.name)
        allowed, why = self.s.discovery._may_poll(self.source)
        self.assertFalse(allowed)
        self.assertIn("rate limit", why)

    def test_a_poll_after_the_interval_is_allowed_again(self):
        past = datetime.datetime.utcnow() - datetime.timedelta(
            seconds=self.source.min_poll_interval_seconds + 60)
        self.stamp(past.isoformat(timespec="seconds"))
        allowed, why = self.s.discovery._may_poll(self.source)
        self.assertTrue(allowed, why)

    def test_a_timestamp_in_the_future_refuses_rather_than_polls(self):
        """A clock nobody can trust is not a licence to fetch."""
        ahead = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        self.stamp(ahead.isoformat(timespec="seconds"))
        allowed, why = self.s.discovery._may_poll(self.source)
        self.assertFalse(allowed)
        self.assertIn("future", why)

    def test_an_unreadable_timestamp_refuses_rather_than_polls(self):
        """A corrupt record must not become permission to hammer an API."""
        self.stamp("not-a-time")
        allowed, why = self.s.discovery._may_poll(self.source)
        self.assertFalse(allowed)
        self.assertIn("unreadable", why)

    def test_the_poll_is_stamped_even_when_it_returns_nothing(self):
        """A fetch that found no work still spent a request. Recording only
        successful fetches would let an empty result reset the clock."""
        self.assertEqual(self.state()["last_polled_at"], "")
        self.s.discovery.poll(self.source.name)
        self.assertNotEqual(self.state()["last_polled_at"], "",
                            "a poll left no record that it had happened")

    def test_polling_twice_in_a_row_is_refused_the_second_time(self):
        """The end-to-end property, and the refusal is on the record.

        Counting only rate-limit refusals, because the first poll is also
        refused -- by the Action Gate, since this deployment is in simulation
        with an empty allowlist. That refusal is correct and belongs to a
        different control, and a test that counted both would pass or fail for
        whichever one happened to change.
        """
        before = len(self.s.audit.events())
        self.s.discovery.poll(self.source.name)
        self.s.discovery.poll(self.source.name)
        refusals = [e for e in self.s.audit.events()[before:]
                    if e.get("event") == "discovery.poll_refused"
                    and "rate limit" in (e.get("why") or "")]
        self.assertEqual(len(refusals), 1,
                         "the second poll was not refused by the rate limit")

    def test_the_gate_still_decides_whether_the_fetch_may_happen(self):
        """The rate limit is one more gate, not a replacement for the Action
        Gate. A source that passed the interval check still has to be allowed
        out, and in simulation with an empty allowlist it is not."""
        before = len(self.s.audit.events())
        self.s.discovery.poll(self.source.name)
        gate_refusals = [e for e in self.s.audit.events()[before:]
                         if e.get("event") == "discovery.poll_refused"
                         and "rate limit" not in (e.get("why") or "")]
        self.assertTrue(gate_refusals,
                        "the first poll reached the network in simulation")

    def test_a_source_with_no_interval_is_not_held_back(self):
        """Manual and owner-entered sources make no external call, so there is
        no limit to respect and inventing one would be permanent noise."""
        allowed, _why = self.s.discovery._rate_limit_respected(
            self.source, {"min_poll_interval_seconds": 0,
                          "last_polled_at": datetime.datetime.utcnow()
                          .isoformat()})
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
