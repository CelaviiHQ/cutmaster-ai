#!/usr/bin/env python3
"""Exit 0 if CHANGELOG.md's ## [Unreleased] section has a non-empty
'### BREAKING' or '### Added' subsection, else exit 1.

Used by the `surface` job in .github/workflows/ci.yml to enforce that any
change to the MCP tool contract (as captured in tests/surface_snapshot.json)
is acknowledged in the CHANGELOG before the PR can merge. Keep the rule
simple: signature drift without a CHANGELOG note is a silent break, which
is the exact failure mode we're preventing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def main() -> int:
    text = CHANGELOG.read_text()

    m = re.search(r"## \[Unreleased\]\n(.*?)(?=^## \[|\Z)", text, re.DOTALL | re.MULTILINE)
    if not m:
        print("Could not find ## [Unreleased] section in CHANGELOG.md", file=sys.stderr)
        return 2
    unreleased = m.group(1)

    for heading in ("BREAKING", "Added"):
        sub = re.search(rf"### {heading}\n(.*?)(?=^###|\Z)", unreleased, re.DOTALL | re.MULTILINE)
        if sub and sub.group(1).strip():
            print(f"Found non-empty ### {heading} subsection under ## [Unreleased].")
            return 0

    print(
        "No non-empty ### BREAKING or ### Added subsection found under ## [Unreleased].",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
