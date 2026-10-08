#!/usr/bin/env python3
"""Enforce branch coverage separately from pytest-cov's combined percentage."""

import json
import sys
from pathlib import Path


def branch_percent(report: dict) -> float:
    totals = report["totals"]
    branches = totals["num_branches"]
    return 100 * totals["covered_branches"] / branches if branches else 100


def main() -> int:
    percent = branch_percent(json.loads(Path(".coverage.json").read_text()))
    print(f"Branch coverage: {percent:.2f}% (required: 90%)")
    return int(percent < 90)


if __name__ == "__main__":
    sys.exit(main())
