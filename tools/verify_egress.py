#!/usr/bin/env python3
"""Verify the OS-level half of the egress boundary.

Run this in a clean interpreter, outside the test suite: the suite installs the
in-process guard, which correctly forbids spawning the child processes this check
needs.

    python3 tools/verify_egress.py

It answers one question with measured evidence rather than a claim: **does this
host actually deny egress to an isolated worker?** If the namespace check fails,
the Action Gate must not be described as an enforced control here, and P0
external execution stays fail-closed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

PROBE = (
    "import socket\n"
    "try:\n"
    "    s = socket.socket(); s.settimeout(3); s.connect(('1.1.1.1', 443))\n"
    "    print('EGRESS_SUCCEEDED')\n"
    "except OSError as e:\n"
    "    print(f'EGRESS_BLOCKED:{type(e).__name__}:{e}')\n"
)


def run(argv: list[str]) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE:{exc}"
    return (out.stdout + out.stderr).strip()


def main() -> int:
    print("Solvent egress deployment verification")
    print("=" * 52)

    baseline = run([sys.executable, "-c", PROBE])
    print(f"1. unconfined process        : {baseline}")

    if shutil.which("unshare") is None:
        print("2. isolated worker namespace : UNAVAILABLE (no 'unshare' on this host)")
        print("\nRESULT: OS-LEVEL ISOLATION UNVERIFIED — keep egress.simulation_only = true")
        return 2

    isolated = run(["unshare", "-n", sys.executable, "-c", PROBE])
    print(f"2. isolated worker namespace : {isolated}")

    print("\n3. in-process guard          : ", end="")
    guard = run([sys.executable, "-c",
                 "from solvent import egress; egress.install(); print(egress.probe())"])
    print(guard)

    ok = isolated.startswith("EGRESS_BLOCKED")
    print()
    if ok:
        print("RESULT: OS-LEVEL ISOLATION ENFORCED.")
        print("A worker in this namespace cannot reach the network at all, so a rogue")
        print("dependency fails at the kernel rather than at a policy check.")
        print("The Action Gate may be described as enforced *when workers are run this way*.")
    else:
        print("RESULT: OS-LEVEL ISOLATION NOT ENFORCED.")
        print("Do not claim the Action Gate as a control on this host.")
        print("Keep egress.simulation_only = true and resolve blocker B1 before")
        print("enabling real external effects.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
