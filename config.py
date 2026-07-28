"""Centralised configuration for MCP Hub.

Everything site-specific — paths, addresses, credentials, feature flags, the
fleet inventory — is resolved here and nowhere else. No IP address, hostname,
domain or vault identifier belongs anywhere else in the codebase.

Precedence: process environment > `.env` next to this file > defaults.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml

# --- .env loading (no external dependency) ----------------------------------
_ENV_FILE = Path(__file__).resolve().parent / ".env"


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a KEY=VALUE file, without overriding real env."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(_ENV_FILE)


# --- typed getters -----------------------------------------------------------
def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def env_path(name: str, default: str) -> Path:
    return Path(os.path.expanduser(os.environ.get(name) or default))


# =============================================================================
# Core paths
# =============================================================================
HUB_HOME = env_path("MCP_HUB_HOME", str(Path(__file__).resolve().parent))
HOSTS_FILE = Path(env("MCP_HOSTS_FILE") or (HUB_HOME / "hosts.yaml"))
TOPOLOGY_FILE = Path(env("MCP_TOPOLOGY_FILE") or (HUB_HOME / "topology.yaml"))
ENDPOINTS_FILE = Path(env("MCP_ENDPOINTS_FILE") or (HUB_HOME / "endpoints.yaml"))
STATE_DB = Path(env("MCP_STATE_DB") or (HUB_HOME / "state.db"))
LOG_FILE = env_path("MCP_LOG_FILE", str(HUB_HOME / "mcp-hub.log"))

# =============================================================================
# Transport
# =============================================================================
# Default to loopback. Binding 0.0.0.0 publishes remote shell execution on the
# local network; that has to be a deliberate act, not a default. See SECURITY.md.
BIND_ADDR = env("MCP_BIND_ADDR", "127.0.0.1")
PORT = env_int("MCP_PORT", 8000)

# Unguessable path segment. Obscurity, not authentication.
SECRET_PATH = env("MCP_SECRET_PATH", "/mcp")
if not SECRET_PATH.startswith("/"):
    SECRET_PATH = "/" + SECRET_PATH

# Bearer token checked on every request. This is the actual authentication.
AUTH_TOKEN = env("MCP_AUTH_TOKEN")
_AUTH_PROFILES_PATH = env("MCP_AUTH_PROFILES_FILE")
AUTH_PROFILES_FILE = Path(_AUTH_PROFILES_PATH) if _AUTH_PROFILES_PATH else None


def load_access_profiles() -> dict[str, dict[str, Any]]:
    """Load token access profiles from a root-owned JSON file."""
    if AUTH_PROFILES_FILE is None:
        return {}
    with AUTH_PROFILES_FILE.open(encoding="utf-8") as handle:
        data = json.load(handle)
    profiles = data.get("tokens", data)
    if not isinstance(profiles, dict):
        raise ValueError("MCP_AUTH_PROFILES_FILE must contain a token mapping")
    allowed_levels = {"read", "operate", "admin"}
    result = {}
    for token, raw in profiles.items():
        if not isinstance(token, str) or not token or not isinstance(raw, dict):
            raise ValueError("each access profile must map a non-empty token to an object")
        level = str(raw.get("level", "read")).lower()
        if level not in allowed_levels:
            raise ValueError(f"invalid access level for profile {raw.get('name', '<unnamed>')!r}")
        result[token] = {
            "_identity": hashlib.sha256(token.encode()).hexdigest(),
            "name": str(raw.get("name") or "unnamed"),
            "level": level,
            "tools": [str(item) for item in raw.get("tools", ["*"])],
            "hosts": [str(item) for item in raw.get("hosts", ["*"])],
            "tags": [str(item) for item in raw.get("tags", [])],
        }
    return result

# Public hostname the hub is reached on, used for DNS-rebinding protection.
PUBLIC_HOST = env("MCP_PUBLIC_HOST")

ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
if PUBLIC_HOST:
    ALLOWED_HOSTS.append(PUBLIC_HOST)
    ALLOWED_ORIGINS.append("https://%s" % PUBLIC_HOST)

# =============================================================================
# Safety
# =============================================================================
# Global kill-switch: when true every mutating tool refuses to run.
READ_ONLY = env_bool("MCP_READ_ONLY", True)
CONFIRMATION_MODE = env("MCP_CONFIRMATION_MODE", "sensitive").lower()
if CONFIRMATION_MODE not in {"off", "sensitive", "all"}:
    CONFIRMATION_MODE = "sensitive"
CONFIRMATION_TTL_SECONDS = env_int("MCP_CONFIRMATION_TTL_SECONDS", 300)

# Tools blocked by READ_ONLY. Anything that executes code, changes state on a
# host, or writes to a third-party API.
MUTATING_TOOLS = frozenset({
    # shell / execution
    "local_exec", "remote_exec", "fleet_exec", "batch_exec",
    "ct_exec", "ct_write_file", "proxmox_ct_exec", "docker_exec",
    "service_ctl", "job_run", "destroy_resource", "wake_host",
    "ssh_reset_control", "confirm_mutation",
    # cloudflare
    "cloudflare_api", "cloudflare_dns_create", "cloudflare_dns_delete",
    "cloudflare_tunnel_config_update",
    # synology dsm
    "dsm_api", "dsm_power", "dsm_package_control",
    "dsm_download_create", "dsm_download_control", "dsm_relogin",
    # n8n
    "n8n_activate_workflow", "n8n_deactivate_workflow", "n8n_call_webhook",
    # notion
    "notion_create_page", "notion_update_page", "notion_archive_page",
    "notion_append_blocks", "notion_append_table_row", "notion_delete_block",
    "notion_reload_token",
    # vault
    "vault_create_item", "vault_update_item",
    # lm studio
    "lmstudio_load", "lmstudio_unload",
})

DESTRUCTIVE_TOOLS = frozenset({
    "cloudflare_dns_delete",
    "destroy_resource",
    "dsm_power",
    "notion_delete_block",
})

CONFIRMATION_TOOLS = frozenset({
    "cloudflare_dns_delete",
    "cloudflare_tunnel_config_update",
    "ct_write_file",
    "dsm_download_control",
    "dsm_package_control",
    "notion_archive_page",
    "notion_delete_block",
    "service_ctl",
})

# =============================================================================
# SSH
# =============================================================================
SSH_KEY = env_path("MCP_SSH_KEY", "~/.ssh/id_ed25519")
SSH_CONTROL_DIR = env_path("MCP_SSH_CONTROL_DIR", "~/.ssh/mcp-hub-control")
SSH_CONTROL_PATH = str(SSH_CONTROL_DIR / "%r@%h:%p")

# Default host for hypervisor-oriented tools. Must be a key in hosts.yaml.
DEFAULT_HYPERVISOR = env("MCP_DEFAULT_HYPERVISOR", "prox")

DEFAULT_TIMEOUT = env_int("MCP_DEFAULT_TIMEOUT", 60)
MAX_TIMEOUT = env_int("MCP_MAX_TIMEOUT", 300)
MAX_STDOUT_BYTES = env_int("MCP_MAX_STDOUT_BYTES", 200_000)
MAX_STDERR_BYTES = env_int("MCP_MAX_STDERR_BYTES", 50_000)

# =============================================================================
# Secrets provider
# =============================================================================
SECRETS_PROVIDER = env("MCP_SECRETS_PROVIDER", "env").lower()
BW_SERVE_URL = env("BW_SERVE_URL", "http://127.0.0.1:8090")

# =============================================================================
# Optional integrations
# =============================================================================
CLOUDFLARE_ENABLED = env_bool("CLOUDFLARE_ENABLED", False)
CF_API_TOKEN = env("CLOUDFLARE_API_TOKEN")
CF_ACCOUNT_ID = env("CLOUDFLARE_ACCOUNT_ID")
CF_ZONE_ID = env("CLOUDFLARE_ZONE_ID")
CF_API_BASE = env("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")
CF_DEFAULT_TUNNEL_ID = env("CLOUDFLARE_DEFAULT_TUNNEL_ID")

N8N_ENABLED = env_bool("N8N_ENABLED", False)
N8N_API_URL = env("N8N_API_URL", "http://localhost:5678/api/v1").rstrip("/")
N8N_API_KEY = env("N8N_API_KEY")
# Per-integration override; the global READ_ONLY still wins.
N8N_READ_ONLY = env_bool("N8N_READ_ONLY", False) or READ_ONLY

DSM_ENABLED = env_bool("DSM_ENABLED", False)
DSM_WEBAPI_BASE = env("DSM_WEBAPI_BASE", "http://localhost:5000/webapi")
DSM_SESSION_NAME = env("DSM_SESSION_NAME", "FileStation")
DSM_USER = env("DSM_USER")
DSM_PASSWORD = env("DSM_PASSWORD")
DSM_CRED_ITEM_UUID = env("DSM_CRED_ITEM_UUID")

NOTION_ENABLED = env_bool("NOTION_ENABLED", False)
NOTION_TOKEN = env("NOTION_TOKEN")
NOTION_TOKEN_ITEM_UUID = env("NOTION_TOKEN_ITEM_UUID")
NOTION_API_BASE = env("NOTION_API_BASE", "https://api.notion.com/v1")
NOTION_VERSION = env("NOTION_VERSION", "2022-06-28")

LMSTUDIO_ENABLED = env_bool("LMSTUDIO_ENABLED", False)
LMSTUDIO_HOST = env("LMSTUDIO_HOST", "")
LMSTUDIO_PORT = env_int("LMSTUDIO_PORT", 1234)
LMSTUDIO_PUBLIC_ENDPOINT = env("LMSTUDIO_PUBLIC_ENDPOINT")

WOL_ENABLED = env_bool("WOL_ENABLED", False)
WOL_BROADCAST = env("WOL_BROADCAST", "255.255.255.255")


# =============================================================================
# YAML-backed inventory
# =============================================================================
def _load_yaml(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or default


def load_hosts() -> dict[str, dict]:
    """Fleet inventory from hosts.yaml: {name: {hostname, user, port, ...}}."""
    data = _load_yaml(HOSTS_FILE, {})
    hosts = data.get("hosts", data) if isinstance(data, dict) else {}
    return hosts if isinstance(hosts, dict) else {}


def load_topology() -> dict[str, Any]:
    """Optional curated topology overlay (topology.yaml). Empty when absent."""
    return _load_yaml(TOPOLOGY_FILE, {}) or {}


def load_endpoints() -> tuple[list[dict], list[dict]]:
    """Optional HTTP health probes (endpoints.yaml).

    Returns (always_probed, intermittent). Both empty when the file is absent.
    """
    data = _load_yaml(ENDPOINTS_FILE, {}) or {}
    return (
        list(data.get("endpoints") or []),
        list(data.get("intermittent") or []),
    )


def integration_disabled(name: str) -> Optional[dict]:
    """Return an error payload when `name` is not enabled, else None."""
    flags = {
        "cloudflare": CLOUDFLARE_ENABLED,
        "n8n": N8N_ENABLED,
        "dsm": DSM_ENABLED,
        "notion": NOTION_ENABLED,
        "lmstudio": LMSTUDIO_ENABLED,
        "wol": WOL_ENABLED,
    }
    if flags.get(name, True):
        return None
    return {
        "error": "integration '%s' is disabled" % name,
        "hint": "set %s_ENABLED=true in .env and configure its credentials"
                % name.upper(),
    }
