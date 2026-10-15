#!/usr/bin/env python3
"""What this machine actually is, and what it will not say.

The owner runs DeskPilot on a Dipgate VPS costing about $98 a month,
alongside MetaTrader 5. The instruction that shapes this whole file is
"do not infer hardware specifications from the monthly price". A price
is a billing fact. It is not a core count, and a system that guessed
one from the other would be configuring its own limits from a number
on an invoice.

So every line below is measured or is marked UNMEASURABLE, and
UNMEASURABLE is a result rather than a gap to be filled. On a Windows
host most of this section will be unmeasurable through anything
already installed: ``/proc`` does not exist, ``os.getloadavg`` is not
there, and nothing in the standard library reports per-process CPU.
Saying so is the point -- a system that reported those as healthy
because it could not read them would be loading a machine on the
strength of not having looked.

**MetaTrader 5 is read about and never touched.** This tool does not
enumerate processes by name, does not signal anything, does not
suggest stopping anything. Where MT5's footprint is observable at all
it is observable as the gap between what the machine has and what
DeskPilot is using, and that gap is what is reported.

Run it before configuring capacity. The numbers it prints are the only
honest basis for the worker counts, the CPU share and the disk floor
that ``solvent setup capacity`` records -- and where it prints
UNMEASURABLE, the owner is choosing a budget rather than reading one,
which is a different act and worth knowing you are performing.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MEASURED = "MEASURED"
UNMEASURABLE = "UNMEASURABLE"

#: Never inferred from anything. Recorded so the report can say out
#: loud that it did not use it.
MONTHLY_COST_USD = 98.0


def _reading(name, value, unit="", *, note=""):
    return {"name": name, "state": MEASURED if value is not None
            else UNMEASURABLE, "value": value, "unit": unit, "note": note}


def operating_system() -> list:
    return [
        _reading("os", platform.system()),
        _reading("os_release", platform.release()),
        _reading("architecture", platform.machine()),
        _reading("python", platform.python_version()),
    ]


def processors() -> list:
    """Cores, and the three things that are not core counts."""
    out = []
    try:
        logical = os.cpu_count()
    except Exception:
        logical = None
    out.append(_reading(
        "logical_cpus", logical, "cores",
        note="what the OS reports, which on a VPS is what the "
             "hypervisor presents and not necessarily what is "
             "contended for"))
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    out.append(_reading(
        "cpus_available_to_this_process", affinity, "cores",
        note="absent on Windows; where it differs from the count "
             "above, the difference is the part of the machine this "
             "process may not use"))
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):
        load = None
    out.append(_reading(
        "load_average_1m", round(load[0], 2) if load else None, "",
        note="absent on Windows. This is the single most important "
             "unmeasurable on the owner's host: without it, CPU "
             "pressure from MetaTrader 5 cannot be observed and the "
             "owner's CPU share is honoured by limiting workers "
             "instead of by sampling"))
    out.append(_reading(
        "cpu_percent_used", None, "%",
        note="not available from the standard library on any "
             "platform, and psutil is not installed; reported "
             "unmeasurable rather than estimated"))
    return out


def memory() -> list:
    """Total and available, where the host will say."""
    total = available = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            fields = {}
            for line in handle:
                key, _, rest = line.partition(":")
                fields[key.strip()] = rest.strip()
        total = int(fields["MemTotal"].split()[0]) * 1024
        available = int(fields["MemAvailable"].split()[0]) * 1024
    except Exception:
        pass
    return [
        _reading("memory_total_bytes", total, "bytes",
                 note="/proc/meminfo; absent on Windows"),
        _reading("memory_available_bytes", available, "bytes",
                 note="what is free for DeskPilot *and* for MetaTrader 5; "
                      "it is not headroom DeskPilot may take"),
        _reading(
            "memory_used_by_metatrader", None, "bytes",
            note="not observable without enumerating another "
                 "application's processes, which this system does "
                 "not do on a machine it is a guest on"),
    ]


def disk(root: str = "/") -> list:
    try:
        usage = shutil.disk_usage(root)
    except Exception:
        return [_reading("disk_total_bytes", None, "bytes",
                         note=f"{root} could not be read")]
    return [
        _reading("disk_total_bytes", usage.total, "bytes"),
        _reading("disk_free_bytes", usage.free, "bytes",
                 note="the floor in `solvent setup capacity "
                      "--min-free-disk-bytes` should leave room for "
                      "MetaTrader 5's own files, not just DeskPilot's"),
        _reading("disk_used_percent",
                 round(100 * usage.used / usage.total, 1), "%"),
        _reading("disk_io_throughput", None, "bytes/s",
                 note="not measurable without writing a benchmark file "
                      "to the owner's disk, which this does not do "
                      "uninvited"),
    ]


def deskpilot_footprint() -> list:
    """What this process costs, where the host will say."""
    peak = None
    try:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = raw * 1024 if sys.platform.startswith("linux") else raw
    except Exception:
        pass
    return [
        _reading("deskpilot_peak_rss_bytes", peak, "bytes",
                 note="this interpreter only, and `resource` does not "
                      "exist on Windows"),
        _reading("deskpilot_cpu_seconds", _cpu_seconds(), "s"),
    ]


def _cpu_seconds():
    try:
        times = os.times()
        return round(times.user + times.system, 2)
    except Exception:
        return None


def network() -> list:
    return [
        _reading(
            "outbound_policy", "Action Gate + egress allowlist",
            note="DeskPilot makes no outbound request outside an "
                 "Action Gate window; the allowlist is the policy's, "
                 "not this tool's"),
        _reading("bandwidth_limit", None, "bytes/s",
                 note="the host states none and this does not measure "
                      "it by saturating the owner's link"),
    ]


def inspect(root: str = "/") -> dict:
    sections = {
        "operating_system": operating_system(),
        "processors": processors(),
        "memory": memory(),
        "disk": disk(root),
        "deskpilot": deskpilot_footprint(),
        "network": network(),
    }
    readings = [r for group in sections.values() for r in group]
    unmeasurable = [r["name"] for r in readings
                    if r["state"] == UNMEASURABLE]
    return {
        "sections": sections,
        "measured": len(readings) - len(unmeasurable),
        "unmeasurable": unmeasurable,
        "monthly_cost_usd": MONTHLY_COST_USD,
        "inferred_from_cost": [],
        "is_the_owners_vps": False,
        "note": ("nothing here is inferred from the monthly cost; a "
                 "price is a billing fact and not a core count"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = inspect(args.root)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    for section, readings in result["sections"].items():
        print(f"\n{section.replace('_', ' ').upper()}")
        for reading in readings:
            if reading["state"] == UNMEASURABLE:
                shown = "UNMEASURABLE"
            elif reading["unit"] == "bytes":
                shown = f"{reading['value'] / 1e9:.2f} GB"
            else:
                shown = f"{reading['value']}{reading['unit']}"
            print(f"  {reading['name']:34s} {shown}")
            if reading["note"]:
                for line in _wrap(reading["note"], 66):
                    print(f"       {line}")
    print(f"\n{result['measured']} reading(s) measured, "
          f"{len(result['unmeasurable'])} unmeasurable.")
    print(f"Nothing inferred from the ${result['monthly_cost_usd']:.0f}/month "
          "cost: a price is not a core count.")
    return 0


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
