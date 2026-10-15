"""Backpressure: the resources DeskPilot yields to, and the ones it cannot see.

The honest finding behind this file is that on the Windows VPS this
system targets, CPU and memory are not measurable through anything
already installed -- `diagnostics._memory()` reads /proc/meminfo and the
load average comes from `os.getloadavg()`, and neither exists on
Windows. So the owner's CPU share is honoured by limiting workers, not
by sampling a machine MetaTrader 5 is also using, and an unmeasured
signal reports as unmeasured rather than as healthy.
"""

from __future__ import annotations

import tempfile
import unittest

from solvent import capacity as cap
from solvent.harness import OWNER, Solvent
from tests_solvent.fixtures import source_names

BUDGET = {"max_owed_seconds": 600.0, "max_concurrent_jobs": 3,
          "max_open_bids": 2, "cpu_share": 0.5,
          "min_free_disk_bytes": 1_000_000_000,
          "max_execution_workers": 0, "max_verification_workers": 0,
          "max_memory_used_percent": 0.0,
          "revision_headroom_fraction": 0.0, "halted": False}


class Base(unittest.TestCase):

    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.s = Solvent(f"{self.box.name}/pressure.db")
        self.addCleanup(self.close)
        self.configure()

    def close(self):
        try:
            self.s.store.close()
        except Exception:
            pass

    def configure(self, **over):
        budget = dict(BUDGET)
        budget.update(over)
        self.s.policy.amend({"capacity": budget}, OWNER, "test: budget")

    def reserve(self, seconds, *, kind=cap.EXECUTE, job="job", key=None):
        return self.s.capacity.reserve(
            seconds, kind=kind, job_id=job,
            idempotency_key=key or f"{kind}:{job}:{seconds}")

    def host(self, **fields):
        self.s.capacity._diagnostics = type(
            "Host", (), {"host": lambda _self: dict(fields)})()

    def signal(self, name):
        for s in self.s.capacity.pressure()["signals"]:
            if s["name"] == name:
                return s
        self.fail(f"no signal named {name!r}")


class SignalsAreReadInTheRightDirection(Base):

    def test_a_floor_signal_is_healthy_when_the_value_is_high(self):
        """Free disk read as a ceiling says FULL on a healthy machine.

        That is what the first version of pressure() did: 32GB free
        against a 1GB floor came out FULL, and -- far worse -- half a
        gigabyte against the same floor came out OK.
        """
        self.host(disk_free_bytes=50_000_000_000)
        self.assertEqual(self.signal("disk_free_bytes")["state"], cap.OK)

    def test_a_floor_signal_is_full_when_the_value_is_low(self):
        self.host(disk_free_bytes=500_000_000)
        self.assertEqual(self.signal("disk_free_bytes")["state"], cap.FULL)

    def test_a_floor_signal_is_tight_as_it_approaches_the_floor(self):
        self.host(disk_free_bytes=1_100_000_000)
        self.assertEqual(self.signal("disk_free_bytes")["state"], cap.TIGHT)

    def test_a_ceiling_signal_is_full_when_the_value_is_high(self):
        self.reserve(600, job="j0", key="e0")
        self.assertEqual(self.signal("owed_seconds")["state"], cap.FULL)

    def test_a_ceiling_signal_is_tight_before_it_is_full(self):
        self.reserve(500, job="j0", key="e0")
        self.assertEqual(self.signal("owed_seconds")["state"], cap.TIGHT)


class SilenceIsNotHealth(Base):

    def test_cpu_is_never_reported_as_healthy(self):
        """It is never sampled, so there is nothing to call healthy."""
        self.assertEqual(self.signal("cpu")["state"], cap.UNMEASURED)
        self.assertFalse(self.signal("cpu")["measured"])

    def test_memory_is_unmeasured_where_the_host_says_nothing(self):
        self.host(memory={})
        self.assertEqual(self.signal("memory_used_percent")["state"],
                         cap.UNMEASURED)

    def test_memory_is_read_where_the_host_does_say(self):
        self.configure(max_memory_used_percent=90.0)
        self.host(memory={"used_percent": 95.0})
        self.assertEqual(self.signal("memory_used_percent")["state"],
                         cap.FULL)

    def test_a_measured_value_with_no_threshold_is_not_called_ok(self):
        """Nothing checked it, so nothing may call it fine."""
        self.host(database_bytes=10_000_000)
        self.assertEqual(self.signal("database_bytes")["state"], cap.INFO)

    def test_the_unmeasured_list_names_what_cannot_be_seen(self):
        self.host(disk_free_bytes=50_000_000_000, memory={})
        pressure = self.s.capacity.pressure()
        self.assertIn("cpu", pressure["unmeasured"])
        self.assertIn("memory_used_percent", pressure["unmeasured"])

    def test_an_unmeasured_signal_never_makes_the_whole_reading_ok(self):
        """OK must come from readings, not from the absence of them."""
        self.host(disk_free_bytes=100_000)
        self.assertEqual(self.s.capacity.pressure()["state"], cap.FULL)


class WorkersAreTheCpuControl(Base):

    def test_the_cpu_share_is_not_derived_from_the_machine(self):
        """Deriving workers from os.cpu_count() would claim the VPS.

        Checked over identifiers, because pressure()'s own docstring
        names getloadavg in the course of explaining that it is never
        called -- which is how the first version of this test failed.
        """
        names = source_names("solvent/capacity.py")
        for forbidden in ("cpu_count", "getloadavg", "sched_getaffinity",
                          "WMI", "wmic", "GetSystemInfo", "psutil"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_identifier_scan_would_notice_a_violation(self):
        names = source_names("solvent/capacity.py")
        self.assertIn("pressure", names)
        self.assertIn("workers", names)

    def test_execution_workers_are_limited(self):
        """At start(), not at admission.

        Admission fills a pipeline; starting occupies a worker. The
        limit moved with the thing it bounds, so this now admits three
        jobs and finds that only two of them can run.
        """
        self.configure(max_execution_workers=2, max_owed_seconds=10_000.0,
                       max_concurrent_jobs=4)
        held = []
        for name in ("a", "b", "c"):
            decision = self.reserve(10, job=name, key=name)
            self.assertTrue(decision.allowed)
            held.append(decision.reservation_id)
        started = [self.s.capacity.start(r) for r in held]
        self.assertEqual(sum(1 for d in started if d.allowed), 2)
        self.assertIn("worker", started[-1].why)
        self.assertEqual(self.s.capacity.executing(), 2)

    def test_verification_workers_are_limited_separately(self):
        self.configure(max_execution_workers=4, max_verification_workers=1,
                       max_owed_seconds=10_000.0, max_concurrent_jobs=4)
        self.assertTrue(self.reserve(10, job="a", key="a").allowed)
        self.assertTrue(self.reserve(10, kind=cap.VERIFY, job="a",
                                     key="v1").allowed)
        self.assertFalse(self.reserve(10, kind=cap.VERIFY, job="b",
                                      key="v2").allowed)

    def test_an_unset_worker_limit_falls_back_to_the_job_limit(self):
        """Not to no limit. "Zero means unlimited" is a bug, not a default."""
        budget = self.s.capacity.budget()
        self.assertEqual(budget.max_execution_workers, 0)
        self.assertEqual(budget.workers(cap.EXECUTE),
                         budget.max_concurrent_jobs)
        self.assertEqual(budget.workers(cap.VERIFY),
                         budget.max_concurrent_jobs)

    def test_a_worker_limit_of_zero_does_not_open_the_gate(self):
        """Every reservation here is for one job, so the concurrency
        limit cannot refuse any of them and only the worker fallback
        can. Spreading them over six jobs let the job limit do the
        refusing, and a mutant that made the fallback unlimited
        survived behind it.
        """
        self.configure(max_execution_workers=0, max_concurrent_jobs=2,
                       max_owed_seconds=10_000.0)
        held = [self.reserve(10, job=f"j{n}", key=f"k{n}")
                for n in range(6)]
        # Two pipelines' worth admitted, and with no worker count set
        # the fallback is that same limit rather than no limit.
        self.assertEqual(sum(1 for d in held if d.allowed), 2)
        started = [self.s.capacity.start(d.reservation_id)
                   for d in held if d.allowed]
        self.assertLessEqual(sum(1 for d in started if d.allowed), 2)


class ReliefShedsTheRightThingFirst(Base):

    def test_a_quiet_system_sheds_nothing(self):
        self.host(disk_free_bytes=50_000_000_000)
        self.assertEqual(self.s.capacity.pressure()["relief"], [])

    def test_discovery_is_shed_before_anything_promised(self):
        self.reserve(600, job="j0", key="e0")
        relief = self.s.capacity.pressure()["relief"]
        self.assertEqual(relief[0], "stop speculative discovery")
        self.assertEqual(relief[-1], "finish and verify what is already owed")

    def test_bids_are_shed_before_accepted_work(self):
        self.reserve(600, job="j0", key="e0")
        relief = self.s.capacity.pressure()["relief"]
        self.assertLess(relief.index("send no further bids"),
                        relief.index("accept no further jobs"))

    def test_pressure_agrees_with_the_gate(self):
        self.host(disk_free_bytes=100_000)
        pressure = self.s.capacity.pressure()
        self.assertEqual(pressure["state"], cap.FULL)
        self.assertFalse(pressure["accepting"])
        self.assertFalse(self.s.capacity.room_for(1.0).allowed)


class PressureTakesNoActionOfItsOwn(Base):

    def test_reading_pressure_changes_nothing(self):
        self.reserve(200, job="j0", key="e0")
        before = self.s.capacity.owed_seconds()
        for _ in range(5):
            self.s.capacity.pressure()
        self.assertEqual(self.s.capacity.owed_seconds(), before)
        self.assertEqual(len(self.s.capacity.outstanding()), 1)

    def test_pressure_survives_a_host_that_will_not_answer(self):
        def explode(_self):
            raise OSError("the host refused to say")

        self.s.capacity._diagnostics = type("Broken", (),
                                            {"host": explode})()
        pressure = self.s.capacity.pressure()
        self.assertIn("disk_free_bytes", pressure["unmeasured"])


if __name__ == "__main__":
    unittest.main()
