"""Fleet inventory tools and helpers."""

from __future__ import annotations

from typing import Any

import config
from tools.registry import register_domain

register_domain("inventory", {"list_hosts", "topology", "get_topology"})


def load_hosts() -> dict[str, dict[str, Any]]:
    """Fleet inventory from hosts.yaml: {name: {hostname, user, port, ...}}."""
    return config.load_hosts()


def get_host(name: str) -> dict[str, Any]:
    """Return one configured host or raise a clear error."""
    hosts = load_hosts()
    if name not in hosts:
        raise ValueError(f"Unknown host {name!r}. Configured: {sorted(hosts.keys())}")
    return hosts[name]


def hypervisor_hosts() -> list[str]:
    """Hosts declared as hypervisors in hosts.yaml by role or tag."""
    out = []
    for name, info in load_hosts().items():
        role = str(info.get("role") or "").lower()
        tags = [str(tag).lower() for tag in (info.get("tags") or [])]
        if "hypervisor" in role or "proxmox" in tags or "pve" in tags:
            out.append(name)
    return out


def list_hosts() -> dict[str, Any]:
    """List configured hosts with their connection metadata, roles, and tags."""
    hosts = load_hosts()
    return {
        "count": len(hosts),
        "hosts": {
            name: {
                "hostname": info["hostname"],
                "user": info["user"],
                "port": info.get("port", 22),
                "role": info.get("role", "unknown"),
                "tags": info.get("tags", []),
                "mac": info.get("mac"),
            }
            for name, info in hosts.items()
        },
    }
