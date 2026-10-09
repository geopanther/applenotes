#!/usr/bin/env python
"""Pre-commit hook: revert rc version headings in CHANGELOG.md back to Unreleased.

If bump2version writes a heading like '## 0.1.1-rc1 - 2026-04-16',
this hook reverts it to '## Unreleased' so that only final releases
get permanent version headings in the changelog.
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
RC_HEADING = re.compile(r"^## \[?\d+\.\d+\.\d+-rc\d+\]? - \d{4}-\d{2}-\d{2}$", re.MULTILINE)


def main() -> int:
    if not CHANGELOG.exists():
        return 0

    text = CHANGELOG.read_text()
    # Remove rc heading lines (the "## Unreleased" above is kept by bumpversion)
    new_text, count = RC_HEADING.subn("", text)

    if count > 0:
        # Clean up duplicate blank lines left after removal
        new_text = re.sub(r"\n{3,}", "\n\n", new_text)
        CHANGELOG.write_text(new_text)
        print(f"Removed {count} rc heading(s) from CHANGELOG.md")

    return 0


if __name__ == "__main__":
    sys.exit(main())
