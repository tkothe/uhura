"""Check commit subjects (or pull request titles) against Conventional Commits.

Reads one subject per line on stdin, prints the ones that do not match, and exits 1 if
there were any. Used by CI (.github/workflows/commits.yml); locally:

    git log --no-merges --format=%s origin/main..HEAD | python3 scripts/check_conventional.py
"""

from __future__ import annotations

import re
import sys

TYPES = ("feat", "fix", "docs", "ci", "chore", "refactor", "test", "perf", "build", "style", "revert")
PATTERN = re.compile(rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9][a-z0-9._/-]*\))?!?: \S")


def invalid(subjects: list[str]) -> list[str]:
    """The subjects that are not Conventional Commits; empty lines are ignored."""
    return [s for s in subjects if s.strip() and not PATTERN.match(s)]


def main() -> int:
    bad = invalid(sys.stdin.read().splitlines())
    for subject in bad:
        print(f"not a Conventional Commit: {subject}")
    if bad:
        print(f"Expected 'type(scope): subject' with type one of: {', '.join(TYPES)}.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
