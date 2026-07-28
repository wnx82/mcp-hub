"""Synology DSM protocol metadata and parameter serialization."""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse

from tools.registry import register_domain

register_domain(
    "dsm",
    {
        "dsm_api",
        "dsm_connections",
        "dsm_download_control",
        "dsm_download_create",
        "dsm_download_list",
        "dsm_file_list",
        "dsm_file_search",
        "dsm_health",
        "dsm_logs",
        "dsm_package_control",
        "dsm_packages",
        "dsm_power",
        "dsm_relogin",
        "dsm_shares",
        "dsm_storage",
        "dsm_system_info",
        "dsm_updates",
    },
)

AUTH_ERROR_CODES = frozenset({105, 106, 107, 119})
ERROR_MESSAGES = {
    100: "unknown error",
    101: "invalid or missing parameter",
    102: "API does not exist",
    103: "method does not exist",
    104: "unsupported API version",
    105: "insufficient account permissions",
    106: "session expired",
    107: "session invalidated",
    119: "invalid or expired SID",
    120: "required parameter missing",
    400: "invalid credentials or parameter",
    401: "account disabled",
    402: "account locked",
    403: "two-factor code required",
    404: "invalid two-factor code",
    406: "OTP activation required",
    407: "IP blocked by DSM auto-block",
    408: "path or file not found",
    409: "operation is not permitted on this path",
    414: "task not found",
}


def endpoint(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname or "localhost", parsed.port or default_port


def encode_params(params: Optional[dict], json_style: bool) -> dict[str, str]:
    """Serialize DSM query parameters, including JSON-style string values."""
    result: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            result[key] = "true" if value else "false"
        elif isinstance(value, (list, dict)):
            result[key] = json.dumps(value, ensure_ascii=False)
        elif json_style and isinstance(value, str):
            result[key] = json.dumps(value, ensure_ascii=False)
        else:
            result[key] = str(value)
    return result
