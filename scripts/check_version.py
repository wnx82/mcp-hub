#!/usr/bin/env python3
"""Verify that the version, the changelog, and (optionally) a git tag agree.

    python scripts/check_version.py              # version <-> CHANGELOG
    python scripts/check_version.py v0.2.0       # also check the tag matches

Exits non-zero with a specific reason on any mismatch. Used by CI and by the
release workflow so a tag can never ship a version the changelog never
mentioned.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_FILE = ROOT / "_version.py"

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        fail(f"no __version__ found in {VERSION_FILE.name}")
    return match.group(1)


def changelog_versions() -> list[str]:
    """Released versions, in file order, from '## [x.y.z] - date' headings."""
    text = CHANGELOG.read_text(encoding="utf-8")
    return re.findall(r"^##\s*\[([^\]]+)\]\s*-\s*\d{4}-\d{2}-\d{2}\s*$", text, re.M)


def main() -> int:
    version = read_version()

    if not SEMVER.match(version):
        fail(f"__version__ {version!r} is not valid semver")

    released = changelog_versions()
    if not released:
        fail("CHANGELOG.md has no released section (## [x.y.z] - YYYY-MM-DD)")

    if version not in released:
        fail(
            f"__version__ is {version} but CHANGELOG.md has no "
            f"'## [{version}] - YYYY-MM-DD' section.\n"
            f"       Sections found: {', '.join(released)}\n"
            f"       Add the entry, or bump _version.py back."
        )

    if released[0] != version:
        fail(
            f"CHANGELOG.md lists {released[0]} above {version}; the current "
            f"version must be the topmost released section"
        )

    print(f"ok: version {version} matches the top CHANGELOG entry")

    if len(sys.argv) > 1:
        tag = sys.argv[1]
        expected = f"v{version}"
        if tag != expected:
            fail(f"tag {tag} does not match __version__ (expected {expected})")
        print(f"ok: tag {tag} matches __version__")

    return 0


if __name__ == "__main__":
    sys.exit(main())
