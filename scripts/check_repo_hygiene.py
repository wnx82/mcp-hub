#!/usr/bin/env python3
"""Reject generated Python and release artifacts tracked by Git."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import PurePosixPath

BLOCKED_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
}
BLOCKED_PATTERNS = (
    "*.egg-info/*",
    "*.pyc",
    "*.pyo",
    "*.tar.gz",
    "*.whl",
)


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        check=True,
        text=True,
    )
    offenders = []
    for name in result.stdout.splitlines():
        path = PurePosixPath(name)
        if BLOCKED_PARTS.intersection(path.parts) or any(
            fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_PATTERNS
        ):
            offenders.append(name)
    if offenders:
        print("Generated artifacts tracked by Git:")
        print("\n".join(f"  {name}" for name in offenders))
        return 1
    print("No generated Python or release artifacts are tracked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
