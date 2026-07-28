"""Cloudflare endpoint construction and response extraction."""
from __future__ import annotations

from typing import Any

from tools.registry import register_domain

register_domain(
    "cloudflare",
    {
        "cf_ingress_dump",
        "cloudflare_api",
        "cloudflare_dns_create",
        "cloudflare_dns_delete",
        "cloudflare_dns_list",
        "cloudflare_tunnel_config_get",
        "cloudflare_tunnel_config_update",
        "cloudflare_tunnel_get",
        "cloudflare_tunnels_list",
    },
)


def tunnel_path(account_id: str, tunnel_id: str | None = None) -> str:
    path = f"/accounts/{account_id}/cfd_tunnel"
    return f"{path}/{tunnel_id}" if tunnel_id else path


def tunnel_config_path(account_id: str, tunnel_id: str) -> str:
    return f"{tunnel_path(account_id, tunnel_id)}/configurations"


def dns_records_path(zone_id: str, record_id: str | None = None) -> str:
    path = f"/zones/{zone_id}/dns_records"
    return f"{path}/{record_id}" if record_id else path


def extract_tunnel_config(response: dict[str, Any]) -> dict[str, Any] | None:
    config = (((response.get("data") or {}).get("result") or {}).get("config"))
    return config if isinstance(config, dict) else None
