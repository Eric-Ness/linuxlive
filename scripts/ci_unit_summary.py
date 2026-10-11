#!/usr/bin/env python3
"""Write unit-summary.json for the CI unit job from a pytest JUnit report.

    scripts/ci_unit_summary.py --junit junit.xml --commit SHA \
        --shellcheck ok --compiled testos-ubuntu testos-fedora --out unit-summary.json
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--shellcheck", required=True)
    parser.add_argument("--compiled", nargs="*", default=[])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    # The report comes from this job's own pytest run, not from outside input.
    root = ET.parse(args.junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    summary = {
        "commit": args.commit,
        "tests": total,
        "passed": total - failed - skipped,
        "failed": failed,
        "skipped": skipped,
        "shellcheck": args.shellcheck,
        "compiled": args.compiled,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
