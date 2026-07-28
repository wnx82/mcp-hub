"""Common registry for independently maintained MCP tool domains."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ToolDomain:
    name: str
    tools: frozenset[str]


_DOMAINS: dict[str, ToolDomain] = {}
_TOOL_DOMAINS: dict[str, str] = {}


def register_domain(name: str, tools: Iterable[str]) -> ToolDomain:
    """Register one domain and reject ambiguous tool ownership."""
    normalized = frozenset(tools)
    conflicts = sorted(tool for tool in normalized if tool in _TOOL_DOMAINS)
    if conflicts:
        raise ValueError(f"tools already registered to a domain: {conflicts}")
    domain = ToolDomain(name=name, tools=normalized)
    _DOMAINS[name] = domain
    for tool in normalized:
        _TOOL_DOMAINS[tool] = name
    return domain


def domain_for_tool(tool: str) -> str:
    """Return the owning domain, or core while migration is incomplete."""
    return _TOOL_DOMAINS.get(tool, "core")


def registered_domains() -> tuple[ToolDomain, ...]:
    return tuple(sorted(_DOMAINS.values(), key=lambda domain: domain.name))
