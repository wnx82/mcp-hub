#!/usr/bin/env python3
"""Verify that every registered MCP tool has complete behavior annotations."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
import server


def main() -> int:
    failures = []
    for tool in asyncio.run(server.mcp.list_tools()):
        annotations = tool.annotations
        if annotations is None:
            failures.append(f"{tool.name}: missing annotations")
            continue
        expected_read_only = tool.name not in config.MUTATING_TOOLS
        if annotations.readOnlyHint is not expected_read_only:
            failures.append(f"{tool.name}: incorrect readOnlyHint")
        if annotations.destructiveHint is None or annotations.idempotentHint is None:
            failures.append(f"{tool.name}: incomplete behavior hints")
    if failures:
        print("\n".join(failures))
        return 1
    print("All registered tools have consistent MCP behavior annotations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
