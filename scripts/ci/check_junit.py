"""Fail a CI step when JUnit reports show that tests did not really run (TI-DO-06).

Standard library only:

    python scripts/ci/check_junit.py --no-skips reports/cacheflow-*.xml

Exits 1 when a report is missing, holds no tests, has failures or errors, or
(with --no-skips) has skipped tests. Used on the regression path, where a test
that skips because its database is unreachable must not count as a pass.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET


def summarize(path: str) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", help="JUnit XML files written by pytest --junitxml")
    parser.add_argument("--no-skips", action="store_true", help="treat skipped tests as a failure")
    args = parser.parse_args(argv)

    problems = []
    for path in args.reports:
        try:
            t = summarize(path)
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
    for p in problems:
        print(f"::error::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
