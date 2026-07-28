"""Fleet inventory tools."""

from __future__ import annotations

from typing import Any

import config
from tools.registry import register_domain

register_domain("inventory", {"list_hosts", "topology"})


def list_hosts() -> dict[str, Any]:
    """List configured hosts with their connection metadata, roles, and tags."""
    hosts = config.load_hosts()
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
