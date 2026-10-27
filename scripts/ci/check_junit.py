"""Fail a CI step when JUnit reports show that tests did not really run (TI-DO-06).

Standard library only:

    python scripts/ci/check_junit.py --no-skips reports/cacheflow-*.xml
    python scripts/ci/check_junit.py reports/*.xml --allow-skip "DATABASE_URL is not set"

Exits 1 when a report is missing, holds no tests, or has failures or errors.
Skipped tests fail the check with --no-skips, and with --allow-skip unless the
skip reason matches one of the given regular expressions. Used where a test
that skips because its database is unreachable must not count as a pass.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET


def read(path: str) -> tuple[dict[str, int], list[tuple[str, str]]]:
    """(totals, [(test id, skip reason)]) of one report."""
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    skips = []
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
        for case in suite.iter("testcase"):
            for skipped in case.findall("skipped"):
                reason = " ".join(filter(None, (skipped.get("message"), skipped.text)))
                skips.append((f"{case.get('classname')}::{case.get('name')}", reason))
    return totals, skips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", help="JUnit XML files written by pytest --junitxml")
    parser.add_argument("--no-skips", action="store_true", help="treat every skipped test as a failure")
    parser.add_argument("--allow-skip", action="append", default=[], metavar="REGEX",
                        help="skip reason that is expected; other skips fail (repeatable)")
    args = parser.parse_args(argv)
    allowed = [re.compile(p) for p in args.allow_skip]

    problems = []
    for path in args.reports:
        try:
            t, skips = read(path)
        except (OSError, ET.ParseError) as exc:
            problems.append(f"{path}: unreadable report ({exc})")
            continue
        print(f"{path}: {t['tests']} tests, {t['failures']} failures, {t['errors']} errors, {t['skipped']} skipped")
        if t["tests"] == 0:
            problems.append(f"{path}: no tests ran")
        if t["failures"] or t["errors"]:
            problems.append(f"{path}: {t['failures']} failures, {t['errors']} errors")
        if args.no_skips and t["skipped"]:
            problems.append(f"{path}: {t['skipped']} tests skipped (is TEST_DATABASE_URL reachable?)")
        elif allowed:
            for test, reason in skips:
                if not any(p.search(reason) for p in allowed):
                    problems.append(f"{path}: unexpected skip {test}: {reason.strip()[:200]}")
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
