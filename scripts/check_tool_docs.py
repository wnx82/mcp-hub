#!/usr/bin/env python3
"""Verify that README tool documentation matches FastMCP registration."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server

README = ROOT / "README.md"


def main() -> int:
    content = README.read_text(encoding="utf-8")
    _, separator, remainder = content.partition("## Tool reference")
    if not separator:
        raise SystemExit("README has no 'Tool reference' section")
    section, separator, _ = remainder.partition("## Security")
    if not separator:
        raise SystemExit("README tool reference has no closing 'Security' section")

    documented = set(re.findall(r"`([a-z][a-z0-9_]*)`", section))
    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    missing = sorted(registered - documented)
    unknown = sorted(documented - registered)
    if missing or unknown:
        if missing:
            print("Tools missing from README:", ", ".join(missing))
        if unknown:
            print("Unknown tools documented in README:", ", ".join(unknown))
        return 1

    count_match = re.search(r"\*\*(\d+) tools\*\*", content)
    if not count_match:
        print("README does not declare its tool count")
        return 1
    declared = int(count_match.group(1))
    if declared != len(registered):
        print(f"README declares {declared} tools, but {len(registered)} are registered")
        return 1

    print(f"README documents all {len(registered)} registered tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
