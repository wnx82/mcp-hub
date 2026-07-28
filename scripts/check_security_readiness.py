#!/usr/bin/env python3
"""Catch common secret-leak and publication mistakes before push or release."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKED_TRACKED_FILES = {
    ".env",
    "PROJECT_INSTRUCTIONS.md",
    "auth-profiles.json",
    "endpoints.yaml",
    "hosts.yaml",
    "state.db",
    "topology.yaml",
}

SECRET_PATTERNS = {
    "private key material": re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    ),
    "non-empty MCP auth token": re.compile(
        r"(?m)^MCP_AUTH_TOKEN=[A-Za-z0-9._~-]{12,}\s*$"
    ),
    "non-empty Cloudflare token": re.compile(
        r"(?m)^CLOUDFLARE_API_TOKEN=[A-Za-z0-9._~-]{12,}\s*$"
    ),
    "non-empty Notion token": re.compile(
        r"(?m)^NOTION_TOKEN=[A-Za-z0-9._~-]{12,}\s*$"
    ),
    "non-empty DSM password": re.compile(
        r"(?m)^DSM_PASSWORD=[^\s$\"'`]{12,}\s*$"
    ),
    "hard-coded bearer token": re.compile(
        r"Authorization:\s*Bearer\s+[A-Za-z0-9._~-]{12,}"
    ),
}

TEXT_EXTENSIONS = {
    "",
    ".json",
    ".md",
    ".py",
    ".service",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_probably_text(path: Path) -> bool:
    return path.suffix in TEXT_EXTENSIONS or path.name in {
        ".env.example",
        ".gitignore",
        "Dockerfile",
        "LICENSE",
    }


def tracked_file_failures() -> list[str]:
    failures = []
    for name in git_lines("ls-files"):
        if name in BLOCKED_TRACKED_FILES:
            failures.append(f"tracked private file: {name}")
    return failures


def content_failures() -> list[str]:
    failures = []
    for name in git_lines("ls-files"):
        path = ROOT / name
        if not path.is_file() or not is_probably_text(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{name}: contains {label}")
    return failures


def main() -> int:
    failures = tracked_file_failures() + content_failures()
    if failures:
        print("Security readiness check failed:")
        print("\n".join(f"  - {failure}" for failure in failures))
        print(
            "\nReview tracked files and replace hard-coded secrets with "
            "examples, environment variables, or private local files."
        )
        return 1
    print("Security readiness checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
