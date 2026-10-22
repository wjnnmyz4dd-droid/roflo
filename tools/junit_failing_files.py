#!/usr/bin/env python3
"""Which test files a junit report records a failure in, one per line.

Written because the PowerShell that did this inline reported

    junit records no failing test; the non-zero exit came from somewhere else

over a junit.xml whose own summary line said ``failures=8``, so the
per-file sweep -- the step whose whole job is to produce tracebacks for
the failures -- produced nothing on the one run that had some. A
control that cannot find what it is looking for is a control that
always passes, and the previous one could not be tested anywhere but
on a Windows runner mid-failure.

This can be. It takes a path and prints paths; everything it knows
about junit is in :func:`failing_files`, and
``tests_solvent/test_windows_checkers.py`` drives it over reports
written by hand.

Three shapes are read, because pytest has written all of them:

* ``<testsuites><testsuite><testcase file="...">``
* ``<testsuite><testcase file="...">`` -- no wrapper
* a ``testcase`` with no ``file`` attribute, where the path is
  recovered from ``classname`` (``tests_solvent.test_x.Class.method``)

A testcase counts as failing if it holds a ``failure`` or an ``error``
child. A ``skipped`` child is not a failure and never has been.
"""

from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET


def _from_classname(classname: str) -> str:
    """``tests_solvent.test_x.Class`` -> ``tests_solvent/test_x.py``."""
    parts = [p for p in classname.split(".") if p]
    keep: list[str] = []
    for part in parts:
        keep.append(part)
        if part.startswith("test_") and len(keep) > 1:
            return "/".join(keep) + ".py"
    return ""


def failing_files(xml_text: str) -> list[str]:
    """Every distinct test file with a failing or erroring case in it."""
    root = ET.fromstring(xml_text)
    found: set[str] = set()
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        name = case.get("file") or _from_classname(case.get("classname", ""))
        if name:
            found.add(name.replace("\\", "/"))
    return sorted(found)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: junit_failing_files.py <junit.xml>", file=sys.stderr)
        return 2
    path = pathlib.Path(argv[1])
    if not path.exists():
        print(f"no junit report at {path}", file=sys.stderr)
        return 1
    for name in failing_files(path.read_text(encoding="utf-8")):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
