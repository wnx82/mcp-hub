#!/usr/bin/env python3
"""Reject missing or French model-facing MCP tool documentation."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server

FRENCH_MARKERS = re.compile(
    r"[àâçéèêëîïôùûüÿœ]|\b("
    r"ajoute|avec|cree|dans|defaut|des|ecrit|etat|hote|les|liste|"
    r"met|modele|parallele|recupere|renvoie|recherche|supprime|une|verifie"
    r")\b",
    re.IGNORECASE,
)


def main() -> int:
    failures = []
    for tool in asyncio.run(server.mcp.list_tools()):
        description = (tool.description or "").strip()
        if not description:
            failures.append(f"{tool.name}: missing description")
        elif FRENCH_MARKERS.search(description):
            failures.append(f"{tool.name}: description still contains French")
    if failures:
        print("\n".join(failures))
        return 1
    print("All registered tools have English descriptions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
