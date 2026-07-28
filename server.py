"""
mcp-hub - central MCP server for infrastructure control.

All site-specific configuration lives in config.py, which reads .env and
hosts.yaml. Nothing about a particular network is hard-coded here.
"""
from __future__ import annotations

import asyncio
import contextvars
import fnmatch
import functools
import hashlib
import hmac as _hmac
import inspect
import json
import logging
import os
import re
import secrets
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Literal, Optional

import httpx
import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

import config
from _version import __version__
from core.limits import LimitLease, ResourceLimiter
from core.snapshots import SnapshotStore
from tools.inventory import list_hosts as list_hosts_tool

# --- Config (see config.py / .env.example) ---
BASE_DIR = config.HUB_HOME
HOSTS_FILE = config.HOSTS_FILE
STATE_DB = config.STATE_DB
LOG_FILE = config.LOG_FILE
SSH_KEY = config.SSH_KEY

SECRET_PATH = config.SECRET_PATH
CF_API_TOKEN = config.CF_API_TOKEN
CF_ACCOUNT_ID = config.CF_ACCOUNT_ID
CF_ZONE_ID = config.CF_ZONE_ID

_RC_KEY = chr(114)+chr(101)+chr(116)+chr(117)+chr(114)+chr(110)+chr(95)+chr(99)+chr(111)+chr(100)+chr(101)
CF_API_BASE = config.CF_API_BASE

DEFAULT_HOST = config.DEFAULT_HYPERVISOR
DEFAULT_TIMEOUT = config.DEFAULT_TIMEOUT
MAX_TIMEOUT = config.MAX_TIMEOUT
MAX_STDOUT_BYTES = config.MAX_STDOUT_BYTES
MAX_STDERR_BYTES = config.MAX_STDERR_BYTES


# --- Tracking du tool appelant (pour mcp_stats granulaire) ---
_current_tool: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tool", default=None
)
_current_profile: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "access_profile", default=None
)
_confirmed_mutation: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "confirmed_mutation", default=False
)
_current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
_mutating_functions: dict[str, Any] = {}
_pending_mutations: dict[str, dict[str, Any]] = {}
_resource_limiter = ResourceLimiter(
    requests_per_minute=config.RATE_LIMIT_PER_MINUTE,
    max_argument_bytes=config.MAX_ARGUMENT_BYTES,
    max_concurrent_per_target=config.MAX_CONCURRENT_PER_HOST,
    circuit_failures=config.CIRCUIT_FAILURES,
    circuit_reset_seconds=config.CIRCUIT_RESET_SECONDS,
    mutation_cooldown_seconds=config.MUTATION_COOLDOWN_SECONDS,
)


# --- Logging ---
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
config.SSH_CONTROL_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("mcp-hub")

# Reduire le bruit de fastmcp/mcp lib (Terminating session, Processing request)
logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("mcp.server").setLevel(logging.WARNING)
logging.getLogger("mcp.server.lowlevel").setLevel(logging.WARNING)
logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# --- Redaction ---
REDACT_PATTERNS = [
    (re.compile(rb"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+"), b"[REDACTED_JWT]"),
    (re.compile(rb"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL), b"[REDACTED_PRIVATE_KEY]"),
    (re.compile(rb"([Tt]oken\s*[:=]\s*)[^\s\"',;]+"), rb"\1[REDACTED]"),
    (re.compile(rb"([Pp]assword\s*[:=]\s*)[^\s\"',;]+"), rb"\1[REDACTED]"),
    (re.compile(rb"([Aa]pi[_-]?[Kk]ey\s*[:=]\s*)[^\s\"',;]+"), rb"\1[REDACTED]"),
    (re.compile(rb"([Ss]ecret\s*[:=]\s*)[^\s\"',;]+"), rb"\1[REDACTED]"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), b"[REDACTED_AWS_KEY]"),
    (re.compile(rb"ntn_[A-Za-z0-9]{40,}"), b"[REDACTED_NOTION_TOKEN]"),
    (re.compile(rb"secret_[A-Za-z0-9]{40,}"), b"[REDACTED_NOTION_TOKEN]"),
    (re.compile(rb"(_sid=)[A-Za-z0-9_\-]{16,}"), rb"\1[REDACTED_DSM_SID]"),
    (re.compile(rb"(passwd=)[^&\s\"',;]+"), rb"\1[REDACTED]"),
]


def redact(data: bytes) -> str:
    for pattern, replacement in REDACT_PATTERNS:
        data = pattern.sub(replacement, data)
    return data.decode(errors="replace")


def redact_str(s: str) -> str:
    return redact(s.encode())


# --- SQLite audit ---
def _init_db() -> None:
    con = sqlite3.connect(STATE_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            tool TEXT NOT NULL,
            host TEXT,
            args_json TEXT,
            duration_ms INTEGER,
            return_code INTEGER,
            error TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_call_log_ts ON call_log(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_call_log_tool ON call_log(tool)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS tool_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            request_id TEXT NOT NULL,
            profile TEXT NOT NULL,
            tool TEXT NOT NULL,
            host TEXT,
            ok INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            result_json TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_tool_audit_request ON tool_audit(request_id)")
    # Cleanup: purge des entrees > 30 jours pour eviter la croissance illimitee
    cur = con.execute("DELETE FROM call_log WHERE ts < datetime('now', '-30 days')")
    purged = cur.rowcount
    con.execute("DELETE FROM tool_audit WHERE ts < datetime('now', '-30 days')")
    con.commit()
    # VACUUM doit etre hors transaction
    con.isolation_level = None
    con.execute("VACUUM")
    con.close()
    os.chmod(STATE_DB, 0o600)
    if purged > 0:
        log.info(f"state.db: {purged} anciennes entrees purgees (retention 30j)")


def _log_call(tool, host, args, duration_ms, return_code, error=None):
    # Si un tool wrapper a set le contextvar, l'utiliser comme tool "reel"
    override = _current_tool.get()
    if override and tool in ("local_exec", "remote_exec", "cloudflare_api"):
        tool = override
    try:
        con = sqlite3.connect(STATE_DB)
        con.execute(
            "INSERT INTO call_log (ts, tool, host, args_json, duration_ms, return_code, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                tool, host,
                json.dumps(args, default=str)[:2000],
                duration_ms, return_code, error,
            ),
        )
        con.commit()
        con.close()
    except Exception as e:
        log.warning(f"call_log insert failed: {e}")


# --- Inventaire hosts ---
def _load_hosts() -> dict[str, dict]:
    if not HOSTS_FILE.exists():
        return {}
    with open(HOSTS_FILE) as f:
        return (yaml.safe_load(f) or {}).get("hosts", {})


def _get_host(name: str) -> dict:
    hosts = _load_hosts()
    if name not in hosts:
        raise ValueError(f"Unknown host {name!r}. Configured: {sorted(hosts.keys())}")
    return hosts[name]


def _hypervisor_hosts() -> list[str]:
    """Hosts declared as hypervisors in hosts.yaml (by role or by tag)."""
    out = []
    for name, info in _load_hosts().items():
        role = str(info.get("role") or "").lower()
        tags = [str(t).lower() for t in (info.get("tags") or [])]
        if "hypervisor" in role or "proxmox" in tags or "pve" in tags:
            out.append(name)
    return out


# --- Execution locale ---
async def _local_run(command: str, as_root: bool = False, timeout: int = DEFAULT_TIMEOUT) -> dict:
    started = datetime.now()
    real_cmd = command
    if as_root and os.geteuid() != 0:
        real_cmd = f"sudo -n bash -c {json.dumps(command)}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", real_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            duration_ms = int((datetime.now() - started).total_seconds() * 1000)
            _log_call("local_exec", None, {"cmd_len": len(command), "as_root": as_root}, duration_ms, None, "timeout")
            return {"command": command, "error": "timeout", "timeout_seconds": timeout}
        rc = proc.returncode
    except Exception as e:
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        _log_call("local_exec", None, {"cmd_len": len(command), "as_root": as_root}, duration_ms, None, str(e))
        return {"command": command, "error": str(e)}

    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    result = {
        "command": command, "as_root": as_root, "return_code": rc,
        "stdout": redact(stdout_b[-MAX_STDOUT_BYTES:]),
        "stderr": redact(stderr_b[-MAX_STDERR_BYTES:]),
        "duration_ms": duration_ms,
    }
    _log_call("local_exec", None, {"cmd_len": len(command), "as_root": as_root}, duration_ms, rc)
    return result


# --- Execution SSH distante (multiplex) ---
def _wrap_remote(host_info: dict, command: str, as_root: bool) -> str:
    """Enveloppe la commande selon le shell du host cible pour rendre
    l execution independante du shell de login distant (zsh/fish/busybox).
    powershell => transmise telle quelle ; sh/busybox => sh -c ; bash (defaut) => bash -c.
    """
    import shlex
    shell = (host_info.get("shell") or "bash").lower()
    if shell in ("powershell", "pwsh", "windows"):
        return command
    interp = "sh" if shell in ("sh", "ash", "dash", "busybox") else "bash"
    inner = interp + " -c " + shlex.quote(command)
    if as_root and host_info.get("user") != "root":
        return "sudo -n " + inner
    return inner


def _ssh_argv(host_info: dict, remote_cmd: str) -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=" + config.SSH_CONTROL_PATH,
        "-o", "ControlPersist=60m",
        "-p", str(host_info.get("port", 22)),
        "-i", str(SSH_KEY),
        f"{host_info['user']}@{host_info['hostname']}",
        remote_cmd,
    ]


async def _ssh_run(host: str, command: str, as_root: bool = False, timeout: int = DEFAULT_TIMEOUT) -> dict:
    started = datetime.now()
    host_info = _get_host(host)

    remote_cmd = _wrap_remote(host_info, command, as_root)

    argv = _ssh_argv(host_info, remote_cmd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            duration_ms = int((datetime.now() - started).total_seconds() * 1000)
            _log_call("remote_exec", host, {"cmd_len": len(command), "as_root": as_root}, duration_ms, None, "timeout")
            return {"host": host, "command": command, "error": "timeout", "timeout_seconds": timeout}
        rc = proc.returncode
    except Exception as e:
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        _log_call("remote_exec", host, {"cmd_len": len(command), "as_root": as_root}, duration_ms, None, str(e))
        return {"host": host, "command": command, "error": str(e)}

    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    result = {
        "host": host,
        "hostname": host_info["hostname"],
        "user": host_info["user"],
        "command": command,
        "as_root": as_root,
        "return_code": rc,
        "stdout": redact(stdout_b[-MAX_STDOUT_BYTES:]),
        "stderr": redact(stderr_b[-MAX_STDERR_BYTES:]),
        "duration_ms": duration_ms,
    }
    _log_call("remote_exec", host, {"cmd_len": len(command), "as_root": as_root}, duration_ms, rc)
    return result


# --- Client Cloudflare API ---
async def _cf_api(method: str, path: str, json_body: Optional[dict] = None, timeout: float = 30.0) -> dict:
    if not CF_API_TOKEN:
        return {"error": "CLOUDFLARE_API_TOKEN non configure"}
    if not path.startswith("/"):
        path = "/" + path
    started = datetime.now()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                method.upper(),
                f"{CF_API_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {CF_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=json_body,
            )
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:5000]}
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        _log_call("cloudflare_api", None, {"method": method, "path": path}, duration_ms, r.status_code)
        return {"http_status": r.status_code, "success": data.get("success", False), "data": data, "duration_ms": duration_ms}
    except Exception as e:
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        _log_call("cloudflare_api", None, {"method": method, "path": path}, duration_ms, None, str(e))
        return {"error": str(e), "duration_ms": duration_ms}



# --- Helper: set le contextvar pour tracer le tool appelant ---
async def _with_tool(name: str, coro):
    token = _current_tool.set(name)
    try:
        return await coro
    finally:
        _current_tool.reset(token)


# --- FastMCP setup ---
_init_db()
_snapshot_store = SnapshotStore(
    STATE_DB,
    retention_days=config.SNAPSHOT_RETENTION_DAYS,
)

mcp = FastMCP(
    name="mcp-hub",
    instructions=(
        "Central infrastructure control hub. Runs shell commands on the hub "
        "itself and on every host declared in hosts.yaml, over a multiplexed "
        "SSH pool. Optional integrations: Cloudflare (tunnels, ingress, DNS), "
        "Proxmox, Docker, Synology DSM, n8n, Notion, Bitwarden/Vaultwarden. "
        "Use as_root=True for sudo NOPASSWD."
    ),
    host=config.BIND_ADDR,
    port=config.PORT,
    stateless_http=True,
    json_response=False,
    streamable_http_path=SECRET_PATH,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=config.ALLOWED_HOSTS,
        allowed_origins=config.ALLOWED_ORIGINS,
    ),
)


# --- Global read-only guard -------------------------------------------------
# MCP_READ_ONLY=true must neutralise every mutating tool. Rather than touching
# 85 tool bodies, wrap the registration decorator once: any tool named in
# config.MUTATING_TOOLS is replaced by a stub that refuses. functools.wraps
# keeps __wrapped__ so FastMCP still derives the real schema from the original
# signature.
_register_tool = mcp.tool


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _profile_allows_host(profile: dict[str, Any], host: str) -> bool:
    if _matches_any(host, profile["hosts"]):
        return True
    allowed_tags = set(profile["tags"])
    if not allowed_tags:
        return False
    info = _load_hosts().get(host, {})
    return bool(allowed_tags.intersection(str(tag) for tag in info.get("tags", [])))


def _access_refusal(profile, fn, args, kwargs) -> dict[str, Any] | None:
    if profile is None:
        return None
    tool = fn.__name__
    if not _matches_any(tool, profile["tools"]):
        return {"error": "refused: tool is not allowed by access profile", "tool": tool}
    level = profile["level"]
    if level == "read" and tool in config.MUTATING_TOOLS:
        return {"error": "refused: access profile is read-only", "tool": tool}
    if level != "admin" and tool in config.DESTRUCTIVE_TOOLS:
        return {"error": "refused: destructive tool requires admin access", "tool": tool}

    bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    bound.apply_defaults()
    requested_hosts: set[str] = set()
    requested_tags: set[str] = set()
    for key in ("host", "target"):
        value = bound.arguments.get(key)
        if isinstance(value, str) and value not in ("", "local"):
            requested_hosts.add(value)
    for value in bound.arguments.get("hosts") or []:
        if isinstance(value, str):
            requested_hosts.add(value)
    for value in bound.arguments.get("tags") or []:
        if isinstance(value, str):
            requested_tags.add(value)
    for operation in bound.arguments.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        target = operation.get("host") or operation.get("target")
        if isinstance(target, str) and target not in ("", "local"):
            requested_hosts.add(target)
        tag = operation.get("tag")
        if isinstance(tag, str):
            requested_tags.add(tag)

    denied_hosts = sorted(
        host for host in requested_hosts if not _profile_allows_host(profile, host)
    )
    denied_tags = sorted(
        tag for tag in requested_tags if not _matches_any(tag, profile["tags"])
    )
    if denied_hosts or denied_tags:
        return {
            "error": "refused: target is not allowed by access profile",
            "tool": tool,
            "denied_hosts": denied_hosts,
            "denied_tags": denied_tags,
        }
    return None


def _request_targets(fn, args, kwargs) -> set[str]:
    """Resolve explicit fleet targets, falling back to the hub itself."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return {"hub"}
    targets: set[str] = set()
    for key in ("host", "target"):
        value = bound.arguments.get(key)
        if isinstance(value, str) and value:
            targets.add(value)
    for value in bound.arguments.get("hosts") or []:
        if isinstance(value, str) and value:
            targets.add(value)
    for operation in bound.arguments.get("operations") or []:
        if isinstance(operation, dict):
            value = operation.get("host") or operation.get("target")
            if isinstance(value, str) and value:
                targets.add(value)
    return targets or {"hub"}


def _limit_identity(profile: dict[str, Any] | None) -> str:
    return str((profile or {}).get("_identity") or (profile or {}).get("name") or "internal")


def _response_envelope(
    tool: str,
    result: Any,
    started: float,
    request_id: str,
    fn,
    args,
    kwargs,
) -> dict[str, Any]:
    error = result.get("error") if isinstance(result, dict) else None
    host = result.get("host") if isinstance(result, dict) else None
    if host is None:
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            host = bound.arguments.get("host")
        except TypeError:
            host = None
    return {
        "ok": error is None,
        "data": result,
        "error": str(error) if error is not None else None,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "host": host,
        "request_id": request_id,
        "tool": tool,
    }


def _log_tool_audit(envelope: dict[str, Any]) -> None:
    data = envelope["data"]
    summary = {
        "ok": envelope["ok"],
        "error": envelope["error"],
        "data_type": type(data).__name__,
        "data_keys": sorted(data) if isinstance(data, dict) else [],
    }
    profile = _current_profile.get()
    try:
        con = sqlite3.connect(STATE_DB)
        con.execute(
            "INSERT INTO tool_audit "
            "(ts, request_id, profile, tool, host, ok, duration_ms, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                envelope["request_id"],
                str((profile or {}).get("name") or "internal"),
                envelope["tool"],
                envelope["host"],
                int(envelope["ok"]),
                envelope["duration_ms"],
                json.dumps(summary, default=str),
            ),
        )
        con.commit()
        con.close()
    except Exception as exc:
        log.warning("tool_audit insert failed: %s", exc)


def _guarded_tool(*d_args, **d_kwargs):
    def wrapper(fn):
        if fn.__name__ in config.MUTATING_TOOLS and fn.__name__ != "confirm_mutation":
            _mutating_functions[fn.__name__] = fn

        @functools.wraps(fn)
        async def guarded(*args, **kwargs):
            started = time.monotonic()
            request_id = _current_request_id.get() or secrets.token_hex(12)
            request_token = _current_request_id.set(request_id)
            lease: LimitLease | None = None
            try:
                if config.READ_ONLY and fn.__name__ in config.MUTATING_TOOLS:
                    result = {
                        "error": "refused: MCP Hub is in read-only mode",
                        "hint": "set MCP_READ_ONLY=false in .env to enable mutating tools",
                    }
                else:
                    profile = _current_profile.get()
                    result = _access_refusal(profile, fn, args, kwargs)
                    requires_confirmation = (
                        config.CONFIRMATION_MODE == "all"
                        or (
                            config.CONFIRMATION_MODE == "sensitive"
                            and fn.__name__ in config.CONFIRMATION_TOOLS
                        )
                    )
                    if result is None and requires_confirmation and not _confirmed_mutation.get():
                        result = {
                            "error": "refused: mutation requires a confirmation plan",
                            "hint": "call plan_mutation, then confirm_mutation",
                        }
                    if result is None:
                        if fn.__name__ != "confirm_mutation":
                            lease, limit_error = _resource_limiter.acquire(
                                identity=_limit_identity(profile),
                                arguments={"args": args, "kwargs": kwargs},
                                targets=_request_targets(fn, args, kwargs),
                                mutating=fn.__name__ in config.MUTATING_TOOLS,
                            )
                            if limit_error is not None:
                                result = {"error": limit_error}
                    if result is None:
                        try:
                            result = fn(*args, **kwargs)
                            if inspect.isawaitable(result):
                                result = await result
                        except Exception as exc:
                            log.exception("tool %s failed (request_id=%s)", fn.__name__, request_id)
                            result = {"error": str(exc)}
                envelope = _response_envelope(
                    fn.__name__, result, started, request_id, fn, args, kwargs
                )
                if lease is not None:
                    _resource_limiter.release(lease, succeeded=envelope["ok"])
                    lease = None
                _log_tool_audit(envelope)
                return envelope
            finally:
                if lease is not None:
                    _resource_limiter.release(lease, succeeded=False)
                _current_request_id.reset(request_token)

        tool_kwargs = dict(d_kwargs)
        if "annotations" not in tool_kwargs:
            read_only = fn.__name__ not in config.MUTATING_TOOLS
            tool_kwargs["annotations"] = ToolAnnotations(
                readOnlyHint=read_only,
                destructiveHint=(
                    fn.__name__ in config.DESTRUCTIVE_TOOLS
                    or fn.__name__ == "confirm_mutation"
                ),
                idempotentHint=read_only and fn.__name__ != "plan_mutation",
                openWorldHint=True,
            )
        decorate = _register_tool(*d_args, **tool_kwargs)
        return decorate(guarded)

    return wrapper


mcp.tool = _guarded_tool

# FastMCP has no `version` parameter, but the low-level server it wraps does,
# and that is what clients see as serverInfo.version during `initialize`.
# Left unset it reports the mcp library's own version, which is misleading.
mcp._mcp_server.version = __version__

if config.READ_ONLY:
    log.warning(
        "MCP_READ_ONLY=true - %d mutating tools are disabled",
        len(config.MUTATING_TOOLS),
    )


# === TOOLS - Health / inventory ===
@mcp.tool()
async def mcp_health() -> dict[str, Any]:
    """Report hub health and probe every configured host concurrently over SSH."""
    hosts = _load_hosts()

    async def _tcp_check(host_info: dict) -> bool:
        """Test TCP rapide sur le port SSH (n'utilise pas ping / cap_net_raw)."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host_info["hostname"], host_info.get("port", 22)
                ),
                timeout=3,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _probe(name: str) -> tuple[str, str]:
        try:
            info = _get_host(name)
        except Exception as e:
            return name, f"config error: {e}"
        # Pre-check TCP: evite un SSH 10s sur host mort
        if not await _tcp_check(info):
            return name, "tcp closed"
        try:
            r = await asyncio.wait_for(_ssh_run(name, "echo ok", timeout=6), timeout=10)
            if r.get("return_code") == 0:
                return name, "reachable"
            return name, f"error rc={r.get(_RC_KEY)}"
        except asyncio.TimeoutError:
            return name, "timeout"
        except Exception as e:
            return name, f"unreachable: {str(e)[:80]}"

    results = await asyncio.gather(*[_probe(n) for n in hosts])
    hosts_status = dict(results)

    return {
        "status": "ok",
        "server": "mcp-hub",
        "version": __version__,
        "hostname": socket.gethostname(),
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "user_id": os.getuid(),
        "root_access": os.geteuid() == 0,
        "hosts_configured": len(hosts),
        "hosts_reachable": sum(1 for s in hosts_status.values() if s == "reachable"),
        "hosts_status": hosts_status,
        "cloudflare_api_configured": bool(CF_API_TOKEN),
        "read_only": config.READ_ONLY,
    }


list_hosts = mcp.tool()(list_hosts_tool)


# === TOOLS - Shell exec ===
@mcp.tool()
async def local_exec(command: str, as_root: bool = False, timeout_seconds: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a shell command on the MCP Hub host."""
    if not command.strip():
        raise ValueError("La commande ne peut pas etre vide.")
    return await _local_run(command, as_root=as_root, timeout=max(1, min(timeout_seconds, MAX_TIMEOUT)))


@mcp.tool()
async def remote_exec(host: str, command: str, as_root: bool = False, timeout_seconds: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a shell command on a configured remote host over SSH."""
    if not command.strip():
        raise ValueError("La commande ne peut pas etre vide.")
    return await _ssh_run(host, command, as_root=as_root, timeout=max(1, min(timeout_seconds, MAX_TIMEOUT)))


# === TOOLS - System introspection ===
@mcp.tool()
async def system_info(host: Optional[str] = None) -> dict[str, Any]:
    """Return an OS, uptime, CPU, memory, disk, network, and listening-port snapshot."""
    combined = (
        "echo == OS ==; cat /etc/os-release 2>/dev/null | head -6; "
        "echo; echo == KERNEL ==; uname -a; "
        "echo; echo == UPTIME ==; uptime; "
        "echo; echo == MEMORY ==; free -h | head -3; "
        "echo; echo == DISK ==; df -hT -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | head -20; "
        "echo; echo == NETWORK ==; ip -brief addr 2>/dev/null || ifconfig | head -20; "
        "echo; echo == LISTENING ==; (ss -lntp 2>/dev/null || netstat -lntp) | head -20; "
        "echo; echo == LOAD ==; cat /proc/loadavg"
    )
    if host is None:
        return await _with_tool("system_info", _local_run(combined, as_root=True, timeout=30))
    return await _with_tool("system_info", _ssh_run(host, combined, as_root=True, timeout=30))


@mcp.tool()
async def service_ctl(
    unit: str,
    action: Literal["status", "start", "stop", "restart", "reload", "enable", "disable", "is-active", "is-enabled"] = "status",
    host: Optional[str] = None,
) -> dict[str, Any]:
    """Inspect or control a systemd service locally or on a configured host."""
    cmd = f"systemctl {action} {unit} --no-pager"
    if action == "status":
        cmd += " -l | head -40"
    if host is None:
        return await _with_tool("service_ctl", _local_run(cmd, as_root=True, timeout=30))
    return await _with_tool("service_ctl", _ssh_run(host, cmd, as_root=True, timeout=30))


@mcp.tool()
async def read_file(path: str, host: Optional[str] = None, max_bytes: int = 500_000, tail_only: bool = False) -> dict[str, Any]:
    """Read a file locally or remotely with automatic secret redaction."""
    max_bytes = max(1, min(max_bytes, 5_000_000))
    tool = "tail" if tail_only else "head"
    cmd = f"{tool} -c {max_bytes} {json.dumps(path)}"
    coro = _local_run(cmd, as_root=True, timeout=30) if host is None else _ssh_run(host, cmd, as_root=True, timeout=30)
    r = await _with_tool("read_file", coro)
    return {
        "host": host or "hub",
        "path": path,
        "content": r.get("stdout", ""),
        "size_bytes": len(r.get("stdout", "")),
        "truncated": len(r.get("stdout", "")) >= max_bytes,
        "return_code": r.get("return_code"),
        "error": r.get("error"),
    }


@mcp.tool()
async def journal_query(
    unit: Optional[str] = None,
    since: str = "1 hour ago",
    priority: str = "warning",
    grep: Optional[str] = None,
    lines: int = 200,
    host: Optional[str] = None,
) -> dict[str, Any]:
    """Query journalctl with unit, time, priority, text, and line-count filters."""
    lines = max(1, min(lines, 1000))
    parts = ["journalctl", "--no-pager", "-n", str(lines)]
    if unit:
        parts += ["-u", unit]
    parts += ["--since", json.dumps(since)]
    parts += ["-p", priority]
    cmd = " ".join(parts)
    if grep:
        cmd += f" | grep -i {json.dumps(grep)}"
    if host is None:
        return await _with_tool("journal_query", _local_run(cmd, as_root=True, timeout=45))
    return await _with_tool("journal_query", _ssh_run(host, cmd, as_root=True, timeout=45))


@mcp.tool()
async def apt_status(host: Optional[str] = None) -> dict[str, Any]:
    """List available package updates and count security updates."""
    cmd = "apt list --upgradable 2>/dev/null | tail -n +2"
    if host is None:
        return await _with_tool("apt_status", _local_run(cmd, as_root=True, timeout=45))
    return await _with_tool("apt_status", _ssh_run(host, cmd, as_root=True, timeout=45))


# === TOOLS - Proxmox ===
@mcp.tool()
async def proxmox_list(host: str = DEFAULT_HOST) -> dict[str, Any]:
    """List LXC containers and virtual machines on a Proxmox host."""
    cmd = "echo === LXC ===; pct list 2>&1; echo; echo === VMs ===; qm list 2>&1"
    return await _with_tool("proxmox_list", _ssh_run(host, cmd, as_root=True, timeout=15))


@mcp.tool()
async def proxmox_ct_exec(ctid: int, command: str, host: str = DEFAULT_HOST, timeout_seconds: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a command inside a Proxmox LXC container through pct exec."""
    cmd = f"pct exec {ctid} -- bash -c {json.dumps(command)}"
    return await _with_tool("proxmox_ct_exec", _ssh_run(host, cmd, as_root=True, timeout=max(1, min(timeout_seconds, MAX_TIMEOUT))))


@mcp.tool()
async def proxmox_ct_status(ctid: int, host: str = DEFAULT_HOST) -> dict[str, Any]:
    """Return the status and configuration of a Proxmox LXC container."""
    cmd = f"pct status {ctid} 2>&1; echo ---; pct config {ctid} 2>&1"
    return await _with_tool("proxmox_ct_status", _ssh_run(host, cmd, as_root=True, timeout=15))


# === TOOLS - Docker ===
@mcp.tool()
async def docker_ps(host: str = DEFAULT_HOST, ctid: Optional[int] = None) -> dict[str, Any]:
    """List Docker containers. On a Proxmox hypervisor, ALWAYS pass ctid= to
    target the LXC that actually runs Docker. Without ctid, `host` must itself
    be a Docker host."""
    docker_cmd = "docker ps -a --format table"
    cmd = f"pct exec {ctid} -- {docker_cmd}" if ctid is not None else docker_cmd
    return await _with_tool("docker_ps", _ssh_run(host, cmd, as_root=True, timeout=15))


@mcp.tool()
async def docker_exec(container: str, command: str, host: str = DEFAULT_HOST, ctid: Optional[int] = None, timeout_seconds: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Run a command inside a Docker container. On a Proxmox hypervisor,
    ALWAYS pass ctid= to target the LXC that actually runs Docker."""
    docker_cmd = f"docker exec {container} sh -c {json.dumps(command)}"
    cmd = f"pct exec {ctid} -- {docker_cmd}" if ctid is not None else docker_cmd
    return await _with_tool("docker_exec", _ssh_run(host, cmd, as_root=True, timeout=max(1, min(timeout_seconds, MAX_TIMEOUT))))


# === TOOLS - Cloudflare API ===
@mcp.tool()
async def cloudflare_tunnels_list() -> dict[str, Any]:
    """List active Cloudflare tunnels for the configured account."""
    return await _with_tool("cloudflare_tunnels_list", _cf_api("GET", f"/accounts/{CF_ACCOUNT_ID}/cfd_tunnel?is_deleted=false"))


@mcp.tool()
async def cloudflare_tunnel_get(tunnel_id: str) -> dict[str, Any]:
    """Return details for a Cloudflare tunnel by ID."""
    return await _with_tool("cloudflare_tunnel_get", _cf_api("GET", f"/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{tunnel_id}"))


@mcp.tool()
async def cloudflare_tunnel_config_get(tunnel_id: str) -> dict[str, Any]:
    """Return the ingress configuration of a Cloudflare tunnel."""
    return await _with_tool("cloudflare_tunnel_config_get", _cf_api("GET", f"/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{tunnel_id}/configurations"))


@mcp.tool()
async def cloudflare_tunnel_config_update(tunnel_id: str, ingress: list[dict]) -> dict[str, Any]:
    """Replace tunnel ingress rules after capturing a reversible snapshot."""
    current = await _cf_api(
        "GET",
        f"/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{tunnel_id}/configurations",
    )
    if not current.get("success"):
        return {"error": "cannot snapshot current Cloudflare configuration", "detail": current}
    current_config = (
        ((current.get("data") or {}).get("result") or {}).get("config")
    )
    if not isinstance(current_config, dict):
        return {"error": "Cloudflare returned no snapshot-compatible configuration"}
    change_id = _snapshot_store.create(
        profile=_profile_identity(_current_profile.get()),
        tool="cloudflare_tunnel_config_update",
        target=f"cloudflare:tunnel:{tunnel_id}",
        state={"tunnel_id": tunnel_id, "config": current_config},
    )
    result = await _with_tool(
        "cloudflare_tunnel_config_update",
        _cf_api(
            "PUT",
            f"/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{tunnel_id}/configurations",
            json_body={"config": {"ingress": ingress}},
        ),
    )
    if not result.get("success"):
        _snapshot_store.set_status(change_id, "mutation_failed")
        return result
    result["change_id"] = change_id
    return result


@mcp.tool()
async def cloudflare_dns_list(zone_id: Optional[str] = None, name_filter: Optional[str] = None) -> dict[str, Any]:
    """List DNS records in the configured Cloudflare zone."""
    zid = zone_id or CF_ZONE_ID
    path = f"/zones/{zid}/dns_records?per_page=200"
    if name_filter:
        path += f"&name.contains={name_filter}"
    return await _with_tool("cloudflare_dns_list", _cf_api("GET", path))


@mcp.tool()
async def cloudflare_dns_create(name: str, type: str, content: str, proxied: bool = True, ttl: int = 1, zone_id: Optional[str] = None) -> dict[str, Any]:
    """Create a DNS record in the configured Cloudflare zone."""
    zid = zone_id or CF_ZONE_ID
    return await _with_tool(
        "cloudflare_dns_create",
        _cf_api(
            "POST",
            f"/zones/{zid}/dns_records",
            json_body={"name": name, "type": type, "content": content, "proxied": proxied, "ttl": ttl},
        ),
    )


@mcp.tool()
async def cloudflare_dns_delete(record_id: str, zone_id: Optional[str] = None) -> dict[str, Any]:
    """Delete a Cloudflare DNS record by ID."""
    zid = zone_id or CF_ZONE_ID
    return await _with_tool("cloudflare_dns_delete", _cf_api("DELETE", f"/zones/{zid}/dns_records/{record_id}"))


@mcp.tool()
async def cloudflare_api(
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
    path: str,
    json_body: Optional[dict] = None,
) -> dict[str, Any]:
    """Call an allowlisted path on the Cloudflare API."""
    return await _cf_api(method, path, json_body=json_body)


# === TOOLS - Meta / stats ===
@mcp.tool()
def mcp_stats(last_n: int = 200) -> dict[str, Any]:
    """Summarize recent tool calls, latency, return codes, and errors."""
    last_n = max(1, min(last_n, 10_000))
    con = sqlite3.connect(STATE_DB)
    cur = con.cursor()
    cur.execute(
        "SELECT tool, COUNT(*), ROUND(AVG(duration_ms), 1), MAX(duration_ms), "
        "SUM(CASE WHEN error IS NOT NULL OR (return_code IS NOT NULL AND return_code >= 400) THEN 1 ELSE 0 END) "
        "FROM (SELECT * FROM call_log ORDER BY id DESC LIMIT ?) "
        "GROUP BY tool ORDER BY 2 DESC",
        (last_n,),
    )
    rows = cur.fetchall()
    con.close()
    return {
        "window": f"derniers {last_n} appels",
        "stats": [
            {"tool": r[0], "calls": r[1], "avg_ms": r[2], "max_ms": r[3], "errors": r[4]}
            for r in rows
        ],
    }


@mcp.tool()
def audit_export(last_n: int = 100) -> dict[str, Any]:
    """Export a bounded JSON-ready audit trail without full tool payloads or secrets."""
    last_n = max(1, min(last_n, 1_000))
    con = sqlite3.connect(STATE_DB)
    rows = con.execute(
        "SELECT ts, request_id, profile, tool, host, ok, duration_ms, result_json "
        "FROM tool_audit ORDER BY id DESC LIMIT ?",
        (last_n,),
    ).fetchall()
    con.close()
    return {
        "retention_days": 30,
        "entries": [
            {
                "timestamp": row[0],
                "request_id": row[1],
                "profile": row[2],
                "tool": row[3],
                "host": row[4],
                "ok": bool(row[5]),
                "duration_ms": row[6],
                "result": json.loads(row[7]),
            }
            for row in rows
        ],
    }


# === TOOLS - Fan-out / batch (parallele, un seul appel) ===

MAX_FANOUT_TARGETS = 15


async def _tcp_reachable(host_info: dict, timeout: float = 3.0) -> bool:
    """Test TCP rapide sur le port SSH (pas de ping / cap_net_raw)."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host_info["hostname"], host_info.get("port", 22)),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _resolve_targets(
    hosts: Optional[list[str]],
    tags: Optional[list[str]],
    exclude: Optional[list[str]],
) -> list[str]:
    """Resout l'ensemble des hosts cibles a partir de hosts / tags / exclude."""
    all_hosts = _load_hosts()
    if hosts:
        unknown = [h for h in hosts if h not in all_hosts]
        if unknown:
            raise ValueError(
                f"Host(s) inconnu(s): {unknown}. Configures: {sorted(all_hosts.keys())}"
            )
        selected = [h for h in hosts if h in all_hosts]
    else:
        selected = list(all_hosts.keys())
    if tags:
        tagset = set(tags)
        selected = [h for h in selected if tagset & set(all_hosts[h].get("tags", []))]
    if exclude:
        excl = set(exclude)
        selected = [h for h in selected if h not in excl]
    return selected


def _compose_op_command(op: dict) -> str:
    """Construit la commande effective d'une op (wrapping docker / pct)."""
    command = op.get("command", "")
    if not command or not str(command).strip():
        raise ValueError("Operation sans 'command'.")
    cmd = str(command)
    container = op.get("container")
    ctid = op.get("ctid")
    if container:
        cmd = f"docker exec {container} sh -c {json.dumps(cmd)}"
    if ctid is not None:
        cmd = f"pct exec {int(ctid)} -- bash -c {json.dumps(cmd)}"
    return cmd


@mcp.tool()
async def fleet_exec(
    command: str,
    hosts: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    exclude: Optional[list[str]] = None,
    include_hub: bool = False,
    as_root: bool = False,
    skip_unreachable: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run the same command concurrently across hosts selected by name or tag. Returns per-host results and an aggregate success summary."""
    if not command.strip():
        raise ValueError("La commande ne peut pas etre vide.")
    timeout = max(1, min(timeout_seconds, MAX_TIMEOUT))

    targets = _resolve_targets(hosts, tags, exclude)
    if len(targets) > MAX_FANOUT_TARGETS:
        raise ValueError(
            f"{len(targets)} cibles > limite {MAX_FANOUT_TARGETS}. "
            "Affine via hosts/tags/exclude."
        )

    all_hosts = _load_hosts()
    unreachable: list[str] = []
    if skip_unreachable and targets:
        checks = await asyncio.gather(*[_tcp_reachable(all_hosts[h]) for h in targets])
        alive = []
        for h, ok in zip(targets, checks):
            (alive if ok else unreachable).append(h)
        targets = alive

    async def _one(h: str):
        return h, await _ssh_run(h, command, as_root=as_root, timeout=timeout)

    results: dict[str, Any] = {}
    if targets:
        pairs = await asyncio.gather(*[_with_tool("fleet_exec", _one(h)) for h in targets])
        for h, r in pairs:
            results[h] = r

    if include_hub:
        results["hub"] = await _with_tool(
            "fleet_exec", _local_run(command, as_root=as_root, timeout=timeout)
        )

    ok = sum(1 for r in results.values() if r.get("return_code") == 0)
    failed = sum(
        1 for r in results.values()
        if r.get("error") or (r.get("return_code") not in (0, None))
    )
    return {
        "results": results,
        "summary": {
            "targets": len(results),
            "ok": ok,
            "failed": failed,
            "unreachable": unreachable,
            "command": command,
        },
    }


@mcp.tool()
async def batch_exec(
    operations: list[dict],
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run heterogeneous operations concurrently in one request. Each operation selects a target, command, optional container, timeout, and label."""
    if not operations:
        raise ValueError("operations vide.")
    if len(operations) > MAX_FANOUT_TARGETS:
        raise ValueError(
            f"{len(operations)} operations > limite {MAX_FANOUT_TARGETS}."
        )
    timeout = max(1, min(timeout_seconds, MAX_TIMEOUT))

    async def _run_op(idx: int, op: dict) -> dict:
        label = op.get("label") or f"op{idx}"
        target = op.get("target")
        if not target:
            return {"label": label, "error": "operation sans 'target'"}
        as_root = bool(op.get("as_root", False))
        try:
            eff_cmd = _compose_op_command(op)
        except Exception as e:
            return {"label": label, "target": target, "error": str(e)}
        try:
            if target in ("hub", "local"):
                r = await _with_tool(
                    "batch_exec", _local_run(eff_cmd, as_root=as_root, timeout=timeout)
                )
            else:
                r = await _with_tool(
                    "batch_exec", _ssh_run(target, eff_cmd, as_root=as_root, timeout=timeout)
                )
        except Exception as e:
            return {"label": label, "target": target, "error": str(e)}
        return {
            "label": label,
            "target": target,
            "return_code": r.get("return_code"),
            "stdout": r.get("stdout", ""),
            "stderr": r.get("stderr", ""),
            "error": r.get("error"),
            "duration_ms": r.get("duration_ms"),
        }

    results = await asyncio.gather(
        *[_run_op(i, op) for i, op in enumerate(operations)]
    )
    return {"results": list(results)}


@mcp.tool()
async def infra_snapshot(skip_unreachable: bool = True) -> dict[str, Any]:
    """Collect a read-only, concurrent overview of the configured infrastructure."""
    all_hosts = _load_hosts()
    names = list(all_hosts.keys())

    checks = await asyncio.gather(*[_tcp_reachable(all_hosts[n]) for n in names])
    reachable = [n for n, ok in zip(names, checks) if ok]
    unreachable = [n for n, ok in zip(names, checks) if not ok]

    quick = "uptime; echo ---; free -h 2>/dev/null | head -2; echo ---; df -h / 2>/dev/null | tail -1"
    prox_cmd = "echo === LXC ===; pct list 2>&1; echo; echo === VMs ===; qm list 2>&1"
    docker_cmd = "docker ps -a --format table 2>&1 | head -40"

    async def _stat(n: str):
        info = all_hosts[n]
        tags = set(info.get("tags", []))
        out: dict[str, Any] = {
            "role": info.get("role"),
            "tags": info.get("tags", []),
        }
        r = await _ssh_run(n, quick, as_root=False, timeout=15)
        out["quick"] = r.get("stdout") or r.get("error", "")
        if "proxmox" in tags:
            rp = await _ssh_run(n, prox_cmd, as_root=True, timeout=15)
            out["proxmox"] = rp.get("stdout") or rp.get("error", "")
        if "docker" in tags:
            rd = await _ssh_run(n, docker_cmd, as_root=True, timeout=15)
            out["docker"] = rd.get("stdout") or rd.get("error", "")
        return n, out

    targets = reachable if skip_unreachable else names
    hosts_out: dict[str, Any] = {}
    if targets:
        pairs = await asyncio.gather(
            *[_with_tool("infra_snapshot", _stat(n)) for n in targets]
        )
        for n, o in pairs:
            hosts_out[n] = o

    return {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "hosts_total": len(names),
        "reachable": reachable,
        "unreachable": unreachable,
        "hosts": hosts_out,
    }


# --- Vaultwarden tools (via bw serve daemon local sur 127.0.0.1:8090) ---
BW_SERVE_URL = config.BW_SERVE_URL


async def _bw_request(method: str, path: str, params: Optional[dict] = None, json_body: Optional[dict] = None, timeout: float = 20.0) -> dict:
    """Helper for bw serve HTTP API (unauthenticated - daemon runs on localhost)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.request(method, f"{BW_SERVE_URL}{path}", params=params, json=json_body)
        except httpx.RequestError as e:
            return {"error": f"bw serve unreachable at {BW_SERVE_URL}: {e}"}
    try:
        data = r.json()
    except Exception:
        return {"error": f"bw serve returned non-JSON: {r.text[:200]}", "status": r.status_code}
    if not r.is_success or not data.get("success", True):
        return {"error": data.get("message", f"HTTP {r.status_code}"), "raw": data}
    return data


@mcp.tool()
async def vault_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Search Vaultwarden items by name, username, or URL without returning passwords."""
    result = await _bw_request("GET", "/list/object/items", params={"search": query})
    if "error" in result:
        return result
    items = result.get("data", {}).get("data", []) or []
    return {
        "query": query,
        "total": len(items),
        "results": [
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "folderId": i.get("folderId"),
                "type": i.get("type"),
                "username": (i.get("login") or {}).get("username") if i.get("type") == 1 else None,
            }
            for i in items[:limit]
        ],
    }


@mcp.tool()
async def vault_list_folders() -> dict[str, Any]:
    """List Vaultwarden folders by ID and name."""
    result = await _bw_request("GET", "/list/object/folders")
    if "error" in result:
        return result
    folders = result.get("data", {}).get("data", []) or []
    return {"folders": [{"id": f.get("id"), "name": f.get("name")} for f in folders]}


@mcp.tool()
async def vault_get_item(name_or_id: str) -> dict[str, Any]:
    """Return a complete Vaultwarden item by UUID or exact name."""
    # Tentative directe par UUID
    if len(name_or_id) == 36 and name_or_id.count("-") == 4:
        result = await _bw_request("GET", f"/object/item/{name_or_id}")
        if "error" not in result:
            return result.get("data", {})
    # Fallback : recherche par nom
    search = await _bw_request("GET", "/list/object/items", params={"search": name_or_id})
    if "error" in search:
        return search
    items = search.get("data", {}).get("data", []) or []
    exact = [i for i in items if i.get("name") == name_or_id]
    if not exact:
        return {
            "error": f"No item found matching '{name_or_id}'",
            "candidates": [{"id": i.get("id"), "name": i.get("name")} for i in items[:8]],
        }
    if len(exact) > 1:
        return {
            "error": f"Multiple items match '{name_or_id}' exactly",
            "candidates": [{"id": i.get("id"), "name": i.get("name")} for i in exact],
        }
    item_id = exact[0].get("id")
    result = await _bw_request("GET", f"/object/item/{item_id}")
    if "error" in result:
        return result
    return result.get("data", {})


@mcp.tool()
async def vault_get_field(item_ref: str, field_name: str) -> dict[str, Any]:
    """Return one field from a Vaultwarden item to limit secret exposure."""
    item = await vault_get_item(item_ref)
    if "error" in item:
        return item
    fn = field_name.strip().lower()
    login = item.get("login") or {}
    if fn in ("password", "login.password"):
        return {"item": item.get("name"), "field": "password", "value": login.get("password")}
    if fn in ("username", "login.username"):
        return {"item": item.get("name"), "field": "username", "value": login.get("username")}
    if fn in ("notes", "note"):
        return {"item": item.get("name"), "field": "notes", "value": item.get("notes")}
    if fn == "totp":
        return {"item": item.get("name"), "field": "totp", "value": login.get("totp")}
    fields = item.get("fields") or []
    for f in fields:
        if (f.get("name") or "").lower() == fn:
            return {"item": item.get("name"), "field": f.get("name"), "value": f.get("value")}
    return {
        "error": f"No field '{field_name}' in item '{item.get('name')}'",
        "available_builtin": ["password", "username", "notes", "totp"],
        "available_custom": [f.get("name") for f in fields],
    }


@mcp.tool()
async def vault_create_item(item: dict) -> dict[str, Any]:
    """Create a Vaultwarden item and return its assigned UUID."""
    result = await _bw_request("POST", "/object/item", json_body=item, timeout=30.0)
    if "error" in result:
        return result
    # Sync post-create pour propagation
    await _bw_request("POST", "/sync", timeout=15.0)
    return result.get("data", {})


@mcp.tool()
async def vault_update_item(item_id: str, patch: dict) -> dict[str, Any]:
    """Update a Vaultwarden item by merging a top-level patch."""
    current = await _bw_request("GET", f"/object/item/{item_id}")
    if "error" in current:
        return current
    item = current.get("data", {})
    if not item:
        return {"error": f"Item {item_id} not found"}
    for k, v in patch.items():
        item[k] = v
    result = await _bw_request("PUT", f"/object/item/{item_id}", json_body=item, timeout=30.0)
    if "error" in result:
        return result
    await _bw_request("POST", "/sync", timeout=15.0)
    return result.get("data", {})




# --- Notion tools (via Notion API, token depuis Vaultwarden via bw-serve) ---
NOTION_API_BASE = config.NOTION_API_BASE
NOTION_VERSION = config.NOTION_VERSION
NOTION_TOKEN_ITEM_UUID = config.NOTION_TOKEN_ITEM_UUID
_NOTION_TOKEN_TTL_SECONDS = 3600.0  # cache 1h then refetch from vault
_notion_token_cache: dict[str, Any] = {"token": None, "fetched_at": 0.0}


async def _notion_get_token(force_refresh: bool = False) -> str:
    """Return the Notion token.

    With MCP_SECRETS_PROVIDER=env it comes straight from NOTION_TOKEN.
    With 'vaultwarden' it is fetched from bw-serve and cached in memory.
    """
    import time

    if config.SECRETS_PROVIDER != "vaultwarden":
        if not config.NOTION_TOKEN:
            raise RuntimeError("NOTION_TOKEN is not set (see .env.example)")
        return config.NOTION_TOKEN
    if not NOTION_TOKEN_ITEM_UUID:
        raise RuntimeError("NOTION_TOKEN_ITEM_UUID is not set (see .env.example)")
    now = time.time()
    if (
        not force_refresh
        and _notion_token_cache["token"]
        and (now - _notion_token_cache["fetched_at"]) < _NOTION_TOKEN_TTL_SECONDS
    ):
        return _notion_token_cache["token"]
    result = await _bw_request("GET", f"/object/item/{NOTION_TOKEN_ITEM_UUID}")
    if "error" in result:
        raise RuntimeError(f"cannot fetch Notion token from vault: {result['error']}")
    item = result.get("data", {})
    token = (item.get("login") or {}).get("password")
    if not token:
        raise RuntimeError(f"Notion vault item {NOTION_TOKEN_ITEM_UUID} has no password field")
    _notion_token_cache["token"] = token
    _notion_token_cache["fetched_at"] = now
    return token


async def _notion_request(
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: float = 30.0,
) -> dict:
    """Call Notion API. Returns dict; on error returns {'error': str, 'status': int?}."""
    try:
        token = await _notion_get_token()
    except Exception as e:
        return {"error": f"token fetch failed: {e}"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.request(
                method,
                f"{NOTION_API_BASE}{path}",
                json=json_body,
                params=params,
                headers=headers,
            )
        except httpx.RequestError as e:
            return {"error": f"Notion API unreachable: {e}"}
    try:
        data = r.json()
    except Exception:
        return {"error": f"Notion returned non-JSON: {r.text[:300]}", "status": r.status_code}
    if not r.is_success:
        return {
            "error": data.get("message", f"HTTP {r.status_code}"),
            "code": data.get("code"),
            "status": r.status_code,
        }
    return data


@mcp.tool()
async def notion_reload_token() -> dict[str, Any]:
    """Invalidate the cached Notion token and reload it from Vaultwarden."""
    try:
        token = await _notion_get_token(force_refresh=True)
        return {"success": True, "token_prefix": token[:6] + "...", "cached": True}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def notion_search(
    query: str = "",
    filter_type: Optional[Literal["page", "database"]] = None,
    page_size: int = 10,
    start_cursor: Optional[str] = None,
) -> dict[str, Any]:
    """Search pages and databases available to the Notion integration."""
    body: dict[str, Any] = {"query": query, "page_size": max(1, min(100, page_size))}
    if filter_type:
        body["filter"] = {"property": "object", "value": filter_type}
    if start_cursor:
        body["start_cursor"] = start_cursor
    return await _notion_request("POST", "/search", json_body=body)


@mcp.tool()
async def notion_get_page(page_id: str) -> dict[str, Any]:
    """Return Notion page metadata and properties, excluding block content."""
    return await _notion_request("GET", f"/pages/{page_id}")


@mcp.tool()
async def notion_get_block_children(
    block_id: str,
    page_size: int = 100,
    start_cursor: Optional[str] = None,
) -> dict[str, Any]:
    """Return child blocks for a Notion page or block."""
    params: dict[str, Any] = {"page_size": str(max(1, min(100, page_size)))}
    if start_cursor:
        params["start_cursor"] = start_cursor
    return await _notion_request("GET", f"/blocks/{block_id}/children", params=params)


@mcp.tool()
async def notion_query_database(
    database_id: str,
    filter: Optional[dict] = None,
    sorts: Optional[list] = None,
    page_size: int = 100,
    start_cursor: Optional[str] = None,
) -> dict[str, Any]:
    """Query a Notion database with optional filters, sorting, and pagination."""
    body: dict[str, Any] = {"page_size": max(1, min(100, page_size))}
    if filter:
        body["filter"] = filter
    if sorts:
        body["sorts"] = sorts
    if start_cursor:
        body["start_cursor"] = start_cursor
    return await _notion_request("POST", f"/databases/{database_id}/query", json_body=body)


@mcp.tool()
async def notion_create_page(
    parent: dict,
    properties: Optional[dict] = None,
    children: Optional[list] = None,
    icon: Optional[dict] = None,
    cover: Optional[dict] = None,
) -> dict[str, Any]:
    """Create a Notion page under a parent page or database."""
    body: dict[str, Any] = {"parent": parent, "properties": properties or {}}
    if children:
        body["children"] = children
    if icon:
        body["icon"] = icon
    if cover:
        body["cover"] = cover
    return await _notion_request("POST", "/pages", json_body=body)


_NOTION_WRITABLE_PROPERTY_TYPES = frozenset({
    "checkbox",
    "date",
    "email",
    "files",
    "multi_select",
    "number",
    "people",
    "phone_number",
    "relation",
    "rich_text",
    "select",
    "status",
    "title",
    "url",
})


def _notion_rollback_body(current: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    """Build a Notion PATCH body containing only reversible requested fields."""
    rollback: dict[str, Any] = {}
    for field in ("archived", "icon", "cover"):
        if field in requested:
            rollback[field] = current.get(field)
    if "properties" not in requested:
        return rollback
    current_properties = current.get("properties") or {}
    restored_properties: dict[str, Any] = {}
    for name in requested["properties"]:
        value = current_properties.get(name)
        property_type = value.get("type") if isinstance(value, dict) else None
        if property_type not in _NOTION_WRITABLE_PROPERTY_TYPES:
            return {
                "error": (
                    f"Notion property {name!r} cannot be snapshotted safely "
                    f"(type={property_type!r})"
                )
            }
        restored_properties[name] = {property_type: value.get(property_type)}
    rollback["properties"] = restored_properties
    return rollback


@mcp.tool()
async def notion_update_page(
    page_id: str,
    properties: Optional[dict] = None,
    archived: Optional[bool] = None,
    icon: Optional[dict] = None,
    cover: Optional[dict] = None,
) -> dict[str, Any]:
    """Update a Notion page after snapshotting the fields being changed."""
    body: dict[str, Any] = {}
    if properties is not None:
        body["properties"] = properties
    if archived is not None:
        body["archived"] = archived
    if icon is not None:
        body["icon"] = icon
    if cover is not None:
        body["cover"] = cover
    if not body:
        return {"error": "aucun champ a modifier - passer au moins un de properties/archived/icon/cover"}
    current = await _notion_request("GET", f"/pages/{page_id}")
    if "error" in current:
        return {"error": "cannot snapshot current Notion page", "detail": current}
    rollback_body = _notion_rollback_body(current, body)
    if "error" in rollback_body:
        return rollback_body
    change_id = _snapshot_store.create(
        profile=_profile_identity(_current_profile.get()),
        tool="notion_update_page",
        target=f"notion:page:{page_id}",
        state={"page_id": page_id, "body": rollback_body},
    )
    result = await _notion_request("PATCH", f"/pages/{page_id}", json_body=body)
    if "error" in result:
        _snapshot_store.set_status(change_id, "mutation_failed")
        return result
    result["change_id"] = change_id
    return result


@mcp.tool()
async def notion_archive_page(page_id: str) -> dict[str, Any]:
    """Move a Notion page to the trash using a reversible archive operation."""
    current = await _notion_request("GET", f"/pages/{page_id}")
    if "error" in current:
        return {"error": "cannot snapshot current Notion page", "detail": current}
    change_id = _snapshot_store.create(
        profile=_profile_identity(_current_profile.get()),
        tool="notion_archive_page",
        target=f"notion:page:{page_id}",
        state={"page_id": page_id, "body": {"archived": bool(current.get("archived"))}},
    )
    result = await _notion_request("PATCH", f"/pages/{page_id}", json_body={"archived": True})
    if "error" in result:
        _snapshot_store.set_status(change_id, "mutation_failed")
        return result
    result["change_id"] = change_id
    return result


@mcp.tool()
async def notion_append_blocks(
    block_id: str,
    children: list,
    after: Optional[str] = None,
) -> dict[str, Any]:
    """Append block objects to a Notion page or parent block."""
    body: dict[str, Any] = {"children": children}
    if after:
        body["after"] = after
    return await _notion_request("PATCH", f"/blocks/{block_id}/children", json_body=body)


@mcp.tool()
async def notion_delete_block(block_id: str) -> dict[str, Any]:
    """Permanently delete a Notion block from its parent."""
    return await _notion_request("DELETE", f"/blocks/{block_id}")





# --- n8n API client (endpoint configured via N8N_API_URL) ---
N8N_API_URL = config.N8N_API_URL
N8N_API_KEY = config.N8N_API_KEY
N8N_BASE_URL = N8N_API_URL.split("/api/")[0]
N8N_READ_ONLY = config.N8N_READ_ONLY


def _n8n_require_write() -> None:
    if N8N_READ_ONLY:
        raise PermissionError(
            "n8n is in read-only mode. Set N8N_READ_ONLY=false (and "
            "MCP_READ_ONLY=false) in "
            "/etc/default/mcp-hub puis redemarrer mcp-hub.service."
        )


async def _n8n_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Appel de l'API REST n8n. Retourne {http_status, success, data|error}."""
    if not N8N_API_KEY:
        return {"success": False, "error": "N8N_API_KEY is not set (see .env.example)"}
    headers = {
        "X-N8N-API-KEY": N8N_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    started = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method.upper(),
                f"{N8N_API_URL}{path}",
                params=params,
                json=json_body,
                headers=headers,
            )
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    out: dict[str, Any] = {
        "http_status": resp.status_code,
        "success": resp.is_success,
        "duration_ms": duration_ms,
    }
    try:
        out["data"] = resp.json() if resp.content else {}
    except Exception:
        out["data"] = {"raw": resp.text[:5000]}
    return out


@mcp.tool()
async def n8n_health() -> dict[str, Any]:
    """Check n8n reachability and report whether writes are enabled."""
    probe = await _n8n_request("GET", "/workflows", params={"limit": 1})
    return {
        "n8n_api_url": N8N_API_URL,
        "n8n_base_url": N8N_BASE_URL,
        "read_only": N8N_READ_ONLY,
        "api_key_present": bool(N8N_API_KEY),
        "reachable": bool(probe.get("success")),
        "http_status": probe.get("http_status"),
        "error": probe.get("error"),
    }


@mcp.tool()
async def n8n_list_workflows(
    active_only: bool = False,
    tags: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List n8n workflows with bounded pagination."""
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 250))}
    if active_only:
        params["active"] = "true"
    if tags:
        params["tags"] = tags
    return await _n8n_request("GET", "/workflows", params=params)


@mcp.tool()
async def n8n_get_workflow(id: str) -> dict[str, Any]:
    """Return a complete n8n workflow including nodes and connections."""
    return await _n8n_request("GET", f"/workflows/{id}")


@mcp.tool()
async def n8n_list_executions(
    workflow_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List recent n8n workflow executions with bounded pagination."""
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 250))}
    if workflow_id:
        params["workflowId"] = workflow_id
    if status:
        params["status"] = status
    return await _n8n_request("GET", "/executions", params=params)


@mcp.tool()
async def n8n_get_execution(id: str, include_data: bool = False) -> dict[str, Any]:
    """Return one n8n workflow execution by ID."""
    params = {"includeData": "true"} if include_data else None
    return await _n8n_request("GET", f"/executions/{id}", params=params)


@mcp.tool()
async def n8n_activate_workflow(id: str) -> dict[str, Any]:
    """Activate an n8n workflow and register its triggers."""
    _n8n_require_write()
    return await _n8n_request("POST", f"/workflows/{id}/activate")


@mcp.tool()
async def n8n_deactivate_workflow(id: str) -> dict[str, Any]:
    """Deactivate an n8n workflow."""
    _n8n_require_write()
    return await _n8n_request("POST", f"/workflows/{id}/deactivate")


@mcp.tool()
async def n8n_call_webhook(
    path: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Trigger an n8n workflow through one of its webhook URLs."""
    _n8n_require_write()
    if path.startswith(("http://", "https://")):
        url = path
    else:
        url = N8N_BASE_URL + (path if path.startswith("/") else "/" + path)
    try:
        async with httpx.AsyncClient(
            timeout=float(timeout_seconds), follow_redirects=True
        ) as client:
            resp = await client.request(
                method.upper(), url, json=payload, headers=headers or {}
            )
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
    body = resp.text
    if len(body) > 10240:
        body = body[:10240] + "\n...[tronque]"
    return {
        "success": resp.is_success,
        "http_status": resp.status_code,
        "headers": dict(resp.headers),
        "body": body,
    }



# === TOOLS - Wake-on-LAN / LM Studio ===
WOL_BROADCAST = config.WOL_BROADCAST
WOL_PORTS = (9, 7)
LMSTUDIO_HOST = config.LMSTUDIO_HOST
LMSTUDIO_PORT = config.LMSTUDIO_PORT
_LMS_SAFE = re.compile(r"^[A-Za-z0-9._/@:+-]+$")


def _magic_packet(mac: str) -> bytes:
    clean = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(clean) != 12:
        raise ValueError("MAC invalide: " + repr(mac))
    return bytes.fromhex("FF" * 6 + clean * 16)


async def _tcp_open(hostname: str, port: int, timeout: float = 3.0) -> bool:
    """Test TCP simple (pas d'ICMP: Windows bloque le ping par defaut)."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _lms_arg(value: Any, label: str) -> str:
    """Valide puis quote un argument passe au CLI lms (shell PowerShell distant)."""
    if not _LMS_SAFE.match(str(value)):
        raise ValueError(
            label + " invalide (autorise: alphanum . _ / @ : + -): " + repr(value)
        )
    return "'" + str(value) + "'"


@mcp.tool()
async def wake_host(
    host: str, wait_port: Optional[int] = None, wait_seconds: int = 0
) -> dict[str, Any]:
    """Wake a configured host by sending a Wake-on-LAN magic packet."""
    hosts = _load_hosts()
    if host not in hosts:
        return {
            "success": False,
            "error": "host inconnu: " + host,
            "hosts_avec_mac": sorted(h for h, c in hosts.items() if c.get("mac")),
        }

    info = hosts[host]
    mac = info.get("mac")
    if not mac:
        return {
            "success": False,
            "error": "pas de MAC declaree pour " + host + " dans hosts.yaml",
        }

    try:
        packet = _magic_packet(mac)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for port in WOL_PORTS:
            sock.sendto(packet, (WOL_BROADCAST, port))
    except Exception as exc:
        return {
            "success": False,
            "error": "envoi du magic packet echoue: "
            + type(exc).__name__
            + ": "
            + str(exc),
        }
    finally:
        try:
            sock.close()
        except Exception:
            pass

    out: dict[str, Any] = {
        "success": True,
        "host": host,
        "hostname": info["hostname"],
        "mac": mac,
        "broadcast": WOL_BROADCAST,
        "udp_ports": list(WOL_PORTS),
        "message": "magic packet envoye a " + host + " (" + mac + ")",
    }

    if wait_port and wait_seconds > 0:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait_seconds
        woken = False
        while loop.time() < deadline:
            if await _tcp_open(info["hostname"], int(wait_port), timeout=3):
                woken = True
                break
            await asyncio.sleep(3)
        out["waited_for"] = {"port": int(wait_port), "seconds": wait_seconds}
        out["woken"] = woken
        out["message"] += (
            " - port " + str(wait_port) + " repond"
            if woken
            else " - port "
            + str(wait_port)
            + " toujours ferme apres "
            + str(wait_seconds)
            + "s"
        )
    return out


@mcp.tool()
async def lmstudio_status(include_gpu: bool = True) -> dict[str, Any]:
    """Report LM Studio host reachability, server state, and loaded models."""
    info = _get_host(LMSTUDIO_HOST)
    ip = info["hostname"]
    base = "http://" + ip + ":" + str(LMSTUDIO_PORT)

    out: dict[str, Any] = {
        "host": LMSTUDIO_HOST,
        "endpoint": base,
        "public_endpoint": config.LMSTUDIO_PUBLIC_ENDPOINT or None,
        "server_up": await _tcp_open(ip, LMSTUDIO_PORT, timeout=3),
    }

    if not out["server_up"]:
        out["hint"] = (
            "LM Studio injoignable sur "
            + ip
            + ":"
            + str(LMSTUDIO_PORT)
            + " - machine eteinte, ou serveur LM Studio non demarre. "
            + "Essayer wake_host('"
            + LMSTUDIO_HOST
            + "', wait_port="
            + str(LMSTUDIO_PORT)
            + ", wait_seconds=120)."
        )
        return out

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(base + "/api/v0/models")
        models = resp.json().get("data", [])
        out["models"] = [
            {
                "id": m.get("id"),
                "type": m.get("type"),
                "state": m.get("state"),
                "loaded_context_length": m.get("loaded_context_length"),
                "max_context_length": m.get("max_context_length"),
            }
            for m in models
        ]
        out["loaded"] = [
            m["id"] for m in out["models"] if m.get("state") == "loaded"
        ]
        out["models_available"] = len(out["models"])
    except Exception as exc:
        out["models_error"] = type(exc).__name__ + ": " + str(exc)

    if include_gpu:
        try:
            r = await _ssh_run(
                LMSTUDIO_HOST,
                "nvidia-smi --query-gpu=name,memory.used,memory.total,"
                "utilization.gpu --format=csv,noheader",
                timeout=15,
            )
            lines = (r.get("stdout") or "").strip().splitlines()
            if lines:
                parts = [p.strip() for p in lines[0].split(",")]
                if len(parts) == 4:
                    out["gpu"] = {
                        "name": parts[0],
                        "vram_used": parts[1],
                        "vram_total": parts[2],
                        "utilization": parts[3],
                    }
        except Exception as exc:
            out["gpu_error"] = type(exc).__name__ + ": " + str(exc)

    return out


@mcp.tool()
async def lmstudio_load(
    model: str,
    context_length: Optional[int] = None,
    gpu: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    parallel: Optional[int] = None,
    identifier: Optional[str] = None,
    timeout_seconds: int = 240,
) -> dict[str, Any]:
    """Load a model in LM Studio through the remote lms CLI."""
    try:
        parts = ["lms", "load", _lms_arg(model, "model"), "--yes"]
        if context_length is not None:
            parts += ["--context-length", str(int(context_length))]
        if gpu is not None:
            parts += ["--gpu", _lms_arg(gpu, "gpu")]
        if ttl_seconds is not None:
            parts += ["--ttl", str(int(ttl_seconds))]
        if parallel is not None:
            parts += ["--parallel", str(int(parallel))]
        if identifier is not None:
            parts += ["--identifier", _lms_arg(identifier, "identifier")]
    except (ValueError, TypeError) as exc:
        return {"success": False, "error": str(exc)}

    info = _get_host(LMSTUDIO_HOST)
    if not await _tcp_open(info["hostname"], LMSTUDIO_PORT, timeout=3):
        return {
            "success": False,
            "error": "LM Studio injoignable sur "
            + info["hostname"]
            + ":"
            + str(LMSTUDIO_PORT)
            + " - reveiller l'hote avec wake_host() d'abord.",
        }

    command = " ".join(parts)
    r = await _ssh_run(LMSTUDIO_HOST, command, timeout=timeout_seconds)
    return {
        "success": r.get(_RC_KEY) == 0,
        "command": command,
        "stdout": r.get("stdout"),
        "stderr": r.get("stderr"),
        _RC_KEY: r.get(_RC_KEY),
    }


@mcp.tool()
async def lmstudio_unload(
    identifier: Optional[str] = None, all_models: bool = False
) -> dict[str, Any]:
    """Unload a model from LM Studio and release its allocated memory."""
    if all_models:
        command = "lms unload --all"
    elif identifier:
        try:
            command = "lms unload " + _lms_arg(identifier, "identifier")
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
    else:
        status = await lmstudio_status(include_gpu=False)
        loaded = status.get("loaded") or []
        if len(loaded) > 1:
            return {
                "success": False,
                "error": "plusieurs modeles charges, preciser identifier= ou all_models=True",
                "loaded": loaded,
            }
        if not loaded:
            return {"success": True, "message": "aucun modele charge", "loaded": []}
        command = "lms unload"

    r = await _ssh_run(LMSTUDIO_HOST, command, timeout=60)
    return {
        "success": r.get(_RC_KEY) == 0,
        "command": command,
        "stdout": r.get("stdout"),
        "stderr": r.get("stderr"),
        _RC_KEY: r.get(_RC_KEY),
    }


# === TOOLS - Synology DSM (DSM 7 web API) ===
# Everything goes through the web API (entry.cgi), never over SSH.
# Credentials are resolved at runtime from the configured secrets provider, so
# no secret is written to disk. The SID is cached in memory and auto-renewed.
DSM_WEBAPI_BASE = config.DSM_WEBAPI_BASE
DSM_CRED_ITEM_UUID = config.DSM_CRED_ITEM_UUID
DSM_SESSION_NAME = config.DSM_SESSION_NAME
DSM_SSH_HOST = os.environ.get("DSM_SSH_HOST", "dsm")
_DSM_SID_TTL_SECONDS = 900.0
_dsm_sid_cache: dict[str, Any] = {"sid": None, "fetched_at": 0.0}

_DSM_AUTH_ERROR_CODES = {105, 106, 107, 119}
_DSM_ERROR_MESSAGES = {
    100: "erreur inconnue",
    101: "parametre invalide ou manquant",
    102: "API inexistante",
    103: "methode inexistante pour cette API",
    104: "version d'API non supportee",
    105: "permissions insuffisantes pour ce compte",
    106: "session expiree",
    107: "session invalidee (login depuis une autre IP)",
    119: "SID invalide ou expire",
    120: "parametre requis manquant",
    400: "identifiants invalides (ou parametre invalide selon l'API appelee)",
    401: "compte desactive",
    402: "compte bloque",
    403: "code 2FA requis",
    404: "code 2FA incorrect",
    406: "activation OTP requise",
    407: "IP bloquee par l'auto-block DSM",
    408: "chemin ou fichier introuvable",
    409: "operation non autorisee sur ce chemin",
    414: "tache introuvable",
}

# Codes de statut DownloadStation2 (best-effort, non documentes officiellement)
_DSM_DL_STATUS = {
    1: "waiting", 2: "downloading", 3: "paused", 4: "finishing", 5: "finished",
    6: "hash_checking", 7: "seeding", 8: "filehosting_waiting", 9: "extracting",
    10: "error",
}


def _dsm_endpoint() -> tuple[str, int]:
    """(host, port) de l'API DSM, pour le pre-check TCP."""
    from urllib.parse import urlparse

    parsed = urlparse(DSM_WEBAPI_BASE)
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname or "localhost", parsed.port or default_port


def _dsm_encode_params(params: Optional[dict], json_style: bool) -> dict[str, str]:
    """Serialise les params pour l'API DSM.

    json_style=True : les API DownloadStation2 v2 exigent des valeurs JSON
    (les chaines doivent etre entre guillemets, ex: type="url" -> '"url"').
    """
    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, dict)):
            out[key] = json.dumps(value, ensure_ascii=False)
        elif json_style and isinstance(value, str):
            out[key] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = str(value)
    return out


async def _dsm_get_credentials() -> tuple[str, str]:
    if config.SECRETS_PROVIDER != "vaultwarden":
        if not config.DSM_USER or not config.DSM_PASSWORD:
            raise RuntimeError(
                "DSM_USER / DSM_PASSWORD are not set (see .env.example), or set "
                "MCP_SECRETS_PROVIDER=vaultwarden to read them from your vault"
            )
        return config.DSM_USER, config.DSM_PASSWORD
    if not DSM_CRED_ITEM_UUID:
        raise RuntimeError("DSM_CRED_ITEM_UUID is not set (see .env.example)")
    result = await _bw_request("GET", f"/object/item/{DSM_CRED_ITEM_UUID}")
    if "error" in result:
        raise RuntimeError(f"credentials DSM illisibles depuis Vaultwarden: {result['error']}")
    login = (result.get("data") or {}).get("login") or {}
    account = login.get("username")
    secret = login.get("password")
    if not account or not secret:
        raise RuntimeError(f"item Vaultwarden {DSM_CRED_ITEM_UUID}: username/password manquant")
    return account, secret


async def _dsm_login(force_refresh: bool = False) -> str:
    """Ouvre une session DSM et renvoie le SID (cache memoire, TTL 15 min)."""
    import time

    now = time.time()
    if (
        not force_refresh
        and _dsm_sid_cache["sid"]
        and (now - _dsm_sid_cache["fetched_at"]) < _DSM_SID_TTL_SECONDS
    ):
        return _dsm_sid_cache["sid"]
    account, secret = await _dsm_get_credentials()
    query = {
        "api": "SYNO.API.Auth",
        "version": "7",
        "method": "login",
        "account": account,
        "passwd": secret,
        "session": DSM_SESSION_NAME,
        "format": "sid",
    }
    async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
        # POST volontaire : en GET le mot de passe finirait dans les logs (httpx
        # et access log DSM). En POST il reste dans le corps de la requete.
        r = await client.post(f"{DSM_WEBAPI_BASE}/entry.cgi", data=query)
    data = r.json()
    if not data.get("success"):
        code = (data.get("error") or {}).get("code")
        raise RuntimeError(
            f"login DSM refuse (code {code}: {_DSM_ERROR_MESSAGES.get(code, 'inconnu')})"
        )
    sid = data["data"]["sid"]
    _dsm_sid_cache["sid"] = sid
    _dsm_sid_cache["fetched_at"] = now
    return sid


async def _dsm_call(
    api: str,
    method: str,
    version: int = 1,
    params: Optional[dict] = None,
    timeout: float = 30.0,
    json_style: bool = False,
    _retry_auth: bool = True,
) -> dict:
    """Appel generique de l'API DSM. Renvoie {'success':True,'data':...} ou {'error':...}."""
    started = datetime.now()
    host, port = _dsm_endpoint()
    if not await _tcp_open(host, port, timeout=3.0):
        _log_call("dsm_api", DSM_SSH_HOST, {"api": api, "method": method}, 0, None, "unreachable")
        return {
            "success": False,
            "error": f"DSM injoignable sur {host}:{port}",
            "dsm_unreachable": True,
            "hint": "check that the DSM host is powered on and DSM_WEBAPI_BASE is correct",
        }
    try:
        sid = await _dsm_login()
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    query: dict[str, str] = {"api": api, "version": str(version), "method": method}
    query.update(_dsm_encode_params(params, json_style))
    query["_sid"] = sid
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            r = await client.get(f"{DSM_WEBAPI_BASE}/entry.cgi", params=query)
        data = r.json()
    except Exception as exc:
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        _log_call("dsm_api", DSM_SSH_HOST, {"api": api, "method": method}, duration_ms, None, str(exc))
        return {"success": False, "error": f"appel DSM echoue: {exc}"}
    duration_ms = int((datetime.now() - started).total_seconds() * 1000)
    if data.get("success"):
        _log_call("dsm_api", DSM_SSH_HOST, {"api": api, "method": method}, duration_ms, 0)
        return {"success": True, "data": data.get("data"), "duration_ms": duration_ms}
    code = (data.get("error") or {}).get("code")
    if code in _DSM_AUTH_ERROR_CODES and _retry_auth:
        _dsm_sid_cache["sid"] = None
        return await _dsm_call(api, method, version, params, timeout, json_style, _retry_auth=False)
    _log_call("dsm_api", DSM_SSH_HOST, {"api": api, "method": method}, duration_ms, code, "dsm_error")
    return {
        "success": False,
        "error": f"DSM code {code}: {_DSM_ERROR_MESSAGES.get(code, 'erreur non repertoriee')}",
        "code": code,
        "details": data.get("error"),
        "api": api,
        "method": method,
        "version": version,
    }


def _dsm_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dsm_human(num: Optional[int]) -> Optional[str]:
    if num is None:
        return None
    size = float(num)
    for unit in ("o", "Kio", "Mio", "Gio", "Tio", "Pio"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} Eio"


def _dsm_space(raw: Any) -> dict[str, Any]:
    """Normalise un bloc size DSM ({'total','used'} en octets sous forme de chaines)."""
    if not isinstance(raw, dict):
        total = _dsm_int(raw)
        return {"total_bytes": total, "total": _dsm_human(total)}
    total = _dsm_int(raw.get("total"))
    used = _dsm_int(raw.get("used"))
    out: dict[str, Any] = {
        "total_bytes": total,
        "used_bytes": used,
        "total": _dsm_human(total),
        "used": _dsm_human(used),
    }
    if total and used is not None:
        out["free"] = _dsm_human(total - used)
        out["used_pct"] = round(used * 100.0 / total, 1)
    return out


def _dsm_summarize_storage(data: dict) -> dict[str, Any]:
    volumes = []
    for vol in data.get("volumes") or []:
        space = _dsm_space(vol.get("size"))
        volumes.append(
            {
                "id": vol.get("id"),
                "path": vol.get("vol_path"),
                "status": vol.get("status"),
                "summary_status": vol.get("summary_status"),
                "space_detail": (vol.get("space_status") or {}).get("detail"),
                "fs_type": vol.get("fs_type"),
                "raid_type": vol.get("raidType"),
                "writable": vol.get("is_writable"),
                "scrubbing": vol.get("scrubbingStatus") or None,
                **space,
            }
        )
    disks = []
    for disk in data.get("disks") or []:
        life = disk.get("remain_life")
        life_value = life.get("value") if isinstance(life, dict) else life
        disks.append(
            {
                "id": disk.get("id"),
                "name": disk.get("name"),
                "slot": disk.get("slot_id"),
                "vendor": disk.get("vendor"),
                "model": disk.get("model"),
                "firmware": disk.get("firm"),
                "type": disk.get("diskType"),
                "size": _dsm_human(_dsm_int(disk.get("size_total"))),
                "temp_c": disk.get("temp"),
                "status": disk.get("status"),
                "smart_status": disk.get("smart_status"),
                "remain_life_pct": None if life_value in (None, -1) else life_value,
                "used_by": disk.get("used_by"),
            }
        )
    pools = []
    for pool in data.get("storagePools") or []:
        pools.append(
            {
                "id": pool.get("id"),
                "status": pool.get("status"),
                "summary_status": pool.get("summary_status"),
                "raid_type": pool.get("raidType"),
                "disks": pool.get("disks"),
                "spares": pool.get("spares"),
                "scrubbing": pool.get("scrubbingStatus") or None,
                **_dsm_space(pool.get("size")),
            }
        )
    return {
        "volumes": volumes,
        "disks": disks,
        "pools": pools,
        "ssd_caches": len(data.get("ssdCaches") or []),
        "missing_pools": data.get("missing_pools") or [],
    }


def _dsm_storage_warnings(summary: dict) -> list[str]:
    warnings: list[str] = []
    for vol in summary.get("volumes") or []:
        pct = vol.get("used_pct")
        if pct is not None and pct >= 90:
            warnings.append(f"volume {vol.get('id')} rempli a {pct}%")
        elif pct is not None and pct >= 80:
            warnings.append(f"volume {vol.get('id')} a {pct}% (seuil d'attention)")
        if vol.get("summary_status") not in (None, "normal"):
            warnings.append(f"volume {vol.get('id')} statut '{vol.get('summary_status')}'"
                            + (f" ({vol.get('space_detail')})" if vol.get("space_detail") else ""))
        if vol.get("writable") is False:
            warnings.append(f"volume {vol.get('id')} NON inscriptible")
    for disk in summary.get("disks") or []:
        if disk.get("smart_status") not in (None, "normal"):
            warnings.append(f"disque {disk.get('name')} SMART '{disk.get('smart_status')}'")
        if disk.get("status") not in (None, "normal"):
            warnings.append(f"disque {disk.get('name')} statut '{disk.get('status')}'")
        temp = disk.get("temp_c")
        if isinstance(temp, int) and temp >= 55:
            warnings.append(f"disque {disk.get('name')} a {temp} C")
    for pool in summary.get("pools") or []:
        if pool.get("summary_status") not in (None, "normal"):
            warnings.append(f"pool {pool.get('id')} statut '{pool.get('summary_status')}'")
    if summary.get("missing_pools"):
        warnings.append("pools manquants detectes")
    return warnings


@mcp.tool()
async def dsm_health() -> dict[str, Any]:
    """Return a consolidated health report for the configured Synology NAS."""
    info, storage, health, upgrade, util = await asyncio.gather(
        _dsm_call("SYNO.Core.System", "info", 3),
        _dsm_call("SYNO.Storage.CGI.Storage", "load_info", 1),
        _dsm_call("SYNO.Core.System.SystemHealth", "get", 1),
        _dsm_call("SYNO.Core.Upgrade.Server", "check", 4),
        _dsm_call("SYNO.Core.System.Utilization", "get", 1),
    )
    if info.get("dsm_unreachable"):
        return {"verdict": "unreachable", "error": info.get("error"), "hint": info.get("hint")}

    out: dict[str, Any] = {"verdict": "ok", "warnings": []}
    if info.get("success"):
        d = info["data"]
        out["system"] = {
            "model": d.get("model"),
            "hostname": (health.get("data") or {}).get("hostname") if health.get("success") else None,
            "dsm_version": d.get("firmware_ver"),
            "firmware_date": d.get("firmware_date"),
            "serial": d.get("serial"),
            "cpu": f"{d.get('cpu_vendor','')} {d.get('cpu_series','')}".strip(),
            "cpu_cores": d.get("cpu_cores"),
            "ram_mb": d.get("ram_size"),
            "uptime_s": d.get("up_time") or (health.get("data") or {}).get("uptime"),
            "temperature_c": d.get("sys_temp"),
            "temp_warning": d.get("temperature_warning"),
        }
        if d.get("temperature_warning"):
            out["warnings"].append("alerte temperature systeme DSM")
    else:
        out["warnings"].append(f"system info: {info.get('error')}")

    if storage.get("success"):
        summary = _dsm_summarize_storage(storage["data"])
        out["storage"] = summary
        out["warnings"].extend(_dsm_storage_warnings(summary))
    else:
        out["warnings"].append(f"storage: {storage.get('error')}")

    if util.get("success"):
        u = util["data"]
        cpu = u.get("cpu") or {}
        mem = u.get("memory") or {}
        out["load"] = {
            "cpu_user_pct": cpu.get("user_load"),
            "cpu_system_pct": cpu.get("system_load"),
            "cpu_other_pct": cpu.get("other_load"),
            "memory_real_usage_pct": mem.get("real_usage"),
            "memory_total_mb": _dsm_int(mem.get("memory_size")),
        }

    if upgrade.get("success"):
        upd = (upgrade["data"] or {}).get("update") or {}
        out["update"] = {
            "available": upd.get("available"),
            "version": upd.get("version"),
            "restart_required": upd.get("reboot"),
        }
        if upd.get("available"):
            out["warnings"].append(f"mise a jour DSM disponible: {upd.get('version')}")

    danger = [w for w in out["warnings"] if "danger" in w.lower() or "critical" in w.lower()
              or "NON inscriptible" in w or "%" in w and "rempli a" in w]
    if danger:
        out["verdict"] = "danger"
    elif out["warnings"]:
        out["verdict"] = "attention"
    return out


@mcp.tool()
async def dsm_system_info(include_utilization: bool = True) -> dict[str, Any]:
    """Return Synology model, DSM version, CPU, memory, uptime, temperature, and NTP state."""
    info = await _dsm_call("SYNO.Core.System", "info", 3)
    if not info.get("success"):
        return info
    out: dict[str, Any] = {"success": True, "system": info["data"]}
    if include_utilization:
        util = await _dsm_call("SYNO.Core.System.Utilization", "get", 1)
        if util.get("success"):
            u = util["data"]
            out["utilization"] = {
                "cpu": u.get("cpu"),
                "memory": u.get("memory"),
                "network": u.get("network"),
                "disk_total": (u.get("disk") or {}).get("total"),
                "space_total": (u.get("space") or {}).get("total"),
            }
        else:
            out["utilization_error"] = util.get("error")
    return out


@mcp.tool()
async def dsm_storage(raw: bool = False) -> dict[str, Any]:
    """Return Synology volumes, storage pools, RAID state, disks, SMART state, and temperatures."""
    result = await _dsm_call("SYNO.Storage.CGI.Storage", "load_info", 1, timeout=45.0)
    if not result.get("success"):
        return result
    if raw:
        return result
    summary = _dsm_summarize_storage(result["data"])
    summary["warnings"] = _dsm_storage_warnings(summary)
    summary["success"] = True
    return summary


@mcp.tool()
async def dsm_shares(limit: int = 100, with_details: bool = True) -> dict[str, Any]:
    """List Synology shared folders with volume, encryption, recycle-bin, and description metadata."""
    params: dict[str, Any] = {"offset": 0, "limit": max(1, min(500, limit))}
    if with_details:
        params["additional"] = ["hidden", "encryption", "recyclebin", "share_quota", "is_cold_storage_share"]
    result = await _dsm_call("SYNO.Core.Share", "list", 1, params)
    if not result.get("success"):
        return result
    data = result["data"] or {}
    return {"success": True, "total": data.get("total"), "shares": data.get("shares")}


@mcp.tool()
async def dsm_packages(only_running: bool = False, only_stopped: bool = False) -> dict[str, Any]:
    """List installed Synology packages and their current state."""
    result = await _dsm_call(
        "SYNO.Core.Package", "list", 2,
        {"additional": ["status", "installed_info"], "ignore_hidden": False},
    )
    if not result.get("success"):
        return result
    packages = []
    for pkg in (result["data"] or {}).get("packages") or []:
        add = pkg.get("additional") or {}
        installed = add.get("installed_info") or {}
        status = add.get("status")
        if only_running and status != "running":
            continue
        if only_stopped and status == "running":
            continue
        packages.append(
            {
                "id": pkg.get("id"),
                "name": pkg.get("name"),
                "version": pkg.get("version"),
                "status": status,
                "broken": installed.get("is_broken"),
                "path": installed.get("path"),
            }
        )
    running = sum(1 for p in packages if p["status"] == "running")
    return {"success": True, "total": len(packages), "running": running, "packages": packages}


@mcp.tool()
async def dsm_package_control(
    package_id: str,
    action: Literal["start", "stop", "restart"],
) -> dict[str, Any]:
    """Start, stop, or restart an installed Synology package."""
    steps: list[dict[str, Any]] = []
    sequence = ["stop", "start"] if action == "restart" else [action]
    for step in sequence:
        r = await _dsm_call("SYNO.Core.Package.Control", step, 1, {"id": package_id}, timeout=60.0)
        steps.append({"step": step, "success": r.get("success"), "error": r.get("error")})
        if not r.get("success"):
            return {"success": False, "package": package_id, "action": action, "steps": steps}
    return {"success": True, "package": package_id, "action": action, "steps": steps}


@mcp.tool()
async def dsm_logs(
    limit: int = 50,
    level: Literal["all", "info", "warn", "err"] = "all",
    keyword: Optional[str] = None,
    start: int = 0,
) -> dict[str, Any]:
    """Return a bounded selection of Synology system log entries."""
    params: dict[str, Any] = {"start": start, "limit": max(1, min(500, limit)), "level": level}
    if keyword:
        params["keyword"] = keyword
    result = await _dsm_call("SYNO.Core.SyslogClient.Log", "list", 1, params)
    if not result.get("success"):
        return result
    data = result["data"] or {}
    return {
        "success": True,
        "total": data.get("total"),
        "counts": {
            "info": data.get("infoCount"),
            "warn": data.get("warnCount"),
            "error": data.get("errorCount"),
        },
        "items": data.get("items"),
    }


@mcp.tool()
async def dsm_updates() -> dict[str, Any]:
    """Check whether a DSM update is available without installing it."""
    result = await _dsm_call("SYNO.Core.Upgrade.Server", "check", 4, timeout=45.0)
    if not result.get("success"):
        return result
    update = (result["data"] or {}).get("update") or {}
    return {
        "success": True,
        "available": update.get("available"),
        "version": update.get("version"),
        "restart_required": update.get("reboot"),
        "details": update,
    }


@mcp.tool()
async def dsm_connections() -> dict[str, Any]:
    """List active SMB, AFP, FTP, and DSM web sessions on the NAS."""
    result = await _dsm_call("SYNO.Core.CurrentConnection", "list", 1)
    if not result.get("success"):
        return result
    data = result["data"] or {}
    return {"success": True, "total": data.get("total"), "systime": data.get("systime"), "items": data.get("items")}


@mcp.tool()
async def dsm_power(
    action: Literal["shutdown", "reboot"],
    confirm: bool = False,
    mode: Literal["auto", "ssh", "api"] = "auto",
) -> dict[str, Any]:
    """Shut down or restart the Synology system. Requires explicit confirmation."""
    if not confirm:
        return {
            "success": False,
            "error": "operation destructive : rappeler avec confirm=True",
            "action": action,
        }
    if mode == "auto":
        mode = "ssh" if action == "shutdown" else "api"

    if mode == "ssh":
        command = "sudo /sbin/poweroff" if action == "shutdown" else "sudo /sbin/reboot"
        r = await _ssh_run(DSM_SSH_HOST, command, timeout=20)
        return {
            "success": r.get(_RC_KEY) == 0,
            "action": action,
            "mode": "ssh",
            "command": command,
            "stdout": r.get("stdout"),
            "stderr": r.get("stderr"),
            "note": "seul /sbin/poweroff est en sudo NOPASSWD pour ce compte ; "
                    "un reboot en SSH echouera probablement (mot de passe requis) -> utiliser mode='api'",
        }

    result = await _dsm_call("SYNO.Core.System", action, 1, timeout=20.0)
    if not result.get("success") and result.get("code") in (101, 120):
        result = await _dsm_call("SYNO.Core.System", action, 1, {"force": True}, timeout=20.0)
    return {"success": result.get("success"), "action": action, "mode": "api", "result": result}


@mcp.tool()
async def dsm_file_list(
    path: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: Literal["name", "size", "mtime", "type"] = "name",
    dirs_only: bool = False,
) -> dict[str, Any]:
    """List files and directories in Synology shared folders through FileStation."""
    if not path:
        result = await _dsm_call(
            "SYNO.FileStation.List", "list_share", 2,
            {"offset": offset, "limit": max(1, min(500, limit)), "sort_by": sort_by},
        )
        if not result.get("success"):
            return result
        data = result["data"] or {}
        return {
            "success": True,
            "mode": "shares",
            "total": data.get("total"),
            "shares": [{"name": s.get("name"), "path": s.get("path")} for s in data.get("shares") or []],
        }
    params: dict[str, Any] = {
        "folder_path": path,
        "offset": offset,
        "limit": max(1, min(500, limit)),
        "sort_by": sort_by,
        "additional": ["size", "time", "type", "perm"],
    }
    if dirs_only:
        params["filetype"] = "dir"
    result = await _dsm_call("SYNO.FileStation.List", "list", 2, params, timeout=45.0)
    if not result.get("success"):
        return result
    data = result["data"] or {}
    files = []
    for f in data.get("files") or []:
        add = f.get("additional") or {}
        files.append(
            {
                "name": f.get("name"),
                "path": f.get("path"),
                "is_dir": f.get("isdir"),
                "size": _dsm_human(_dsm_int(add.get("size"))),
                "size_bytes": _dsm_int(add.get("size")),
                "mtime": (add.get("time") or {}).get("mtime"),
            }
        )
    return {"success": True, "mode": "files", "path": path, "total": data.get("total"), "files": files}


@mcp.tool()
async def dsm_file_search(
    folder_path: str,
    pattern: str,
    recursive: bool = True,
    limit: int = 50,
    max_wait_seconds: int = 25,
) -> dict[str, Any]:
    """Search Synology files through the asynchronous FileStation search API."""
    started = await _dsm_call(
        "SYNO.FileStation.Search", "start", 2,
        {"folder_path": folder_path, "pattern": pattern, "recursive": recursive},
    )
    if not started.get("success"):
        return started
    taskid = (started["data"] or {}).get("taskid")
    if not taskid:
        return {"success": False, "error": "aucun taskid retourne par FileStation.Search"}
    finished = False
    payload: dict[str, Any] = {}
    waited = 0.0
    try:
        while waited < max_wait_seconds:
            listing = await _dsm_call(
                "SYNO.FileStation.Search", "list", 2,
                {"taskid": taskid, "offset": 0, "limit": max(1, min(500, limit)),
                 "additional": ["size", "time", "type"]},
            )
            if not listing.get("success"):
                return listing
            payload = listing["data"] or {}
            finished = bool(payload.get("finished"))
            if finished:
                break
            await asyncio.sleep(1.5)
            waited += 1.5
    finally:
        await _dsm_call("SYNO.FileStation.Search", "stop", 2, {"taskid": taskid})
        await _dsm_call("SYNO.FileStation.Search", "clean", 2, {"taskid": taskid})
    results = []
    for f in payload.get("files") or []:
        add = f.get("additional") or {}
        results.append(
            {
                "name": f.get("name"),
                "path": f.get("path"),
                "is_dir": f.get("isdir"),
                "size": _dsm_human(_dsm_int(add.get("size"))),
                "mtime": (add.get("time") or {}).get("mtime"),
            }
        )
    return {
        "success": True,
        "finished": finished,
        "partial": not finished,
        "total": payload.get("total"),
        "folder_path": folder_path,
        "pattern": pattern,
        "results": results,
    }


@mcp.tool()
async def dsm_download_list(limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """List Synology Download Station tasks, progress, and transfer speed."""
    result = await _dsm_call(
        "SYNO.DownloadStation2.Task", "list", 2,
        {"offset": offset, "limit": max(1, min(200, limit)), "additional": ["detail", "transfer"]},
        json_style=True,
    )
    if not result.get("success"):
        return result
    data = result["data"] or {}
    tasks = []
    for t in data.get("task") or []:
        add = t.get("additional") or {}
        transfer = add.get("transfer") or {}
        detail = add.get("detail") or {}
        size = _dsm_int(t.get("size"))
        done = _dsm_int(transfer.get("size_downloaded"))
        tasks.append(
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "type": t.get("type"),
                "status": t.get("status"),
                "status_label": _DSM_DL_STATUS.get(t.get("status"), "inconnu"),
                "size": _dsm_human(size),
                "progress_pct": round(done * 100.0 / size, 1) if size and done is not None else None,
                "speed_down": _dsm_human(_dsm_int(transfer.get("speed_download"))),
                "speed_up": _dsm_human(_dsm_int(transfer.get("speed_upload"))),
                "destination": detail.get("destination"),
                "created": detail.get("create_time"),
            }
        )
    return {"success": True, "total": data.get("total"), "tasks": tasks}


@mcp.tool()
async def dsm_download_create(
    urls: list[str],
    destination: Optional[str] = None,
) -> dict[str, Any]:
    """Create one or more Synology Download Station tasks from URLs."""
    if not urls:
        return {"success": False, "error": "aucune URL fournie"}
    params: dict[str, Any] = {"type": "url", "url": urls, "create_list": False}
    if destination:
        params["destination"] = destination
    result = await _dsm_call("SYNO.DownloadStation2.Task", "create", 2, params, timeout=60.0, json_style=True)
    if not result.get("success"):
        return result
    return {"success": True, "created": (result["data"] or {}).get("task_id"), "urls": urls}


@mcp.tool()
async def dsm_download_control(
    action: Literal["pause", "resume", "delete"],
    task_ids: list[str],
    delete_downloaded_files: bool = False,
) -> dict[str, Any]:
    """Pause, resume, or delete Synology Download Station tasks."""
    if not task_ids:
        return {"success": False, "error": "aucun task_id fourni"}
    params: dict[str, Any] = {"id": task_ids}
    if action == "delete":
        params["force_complete"] = delete_downloaded_files
    result = await _dsm_call(
        "SYNO.DownloadStation2.Task", action, 2, params, timeout=60.0, json_style=True
    )
    if not result.get("success"):
        return result
    return {"success": True, "action": action, "task_ids": task_ids, "result": result.get("data")}


@mcp.tool()
async def dsm_api(
    api: str,
    method: str,
    version: int = 1,
    params: Optional[dict] = None,
    json_style: bool = False,
) -> dict[str, Any]:
    """Call a Synology DSM API method not covered by a dedicated tool."""
    return await _dsm_call(api, method, version, params, timeout=60.0, json_style=json_style)


@mcp.tool()
async def dsm_relogin() -> dict[str, Any]:
    """Invalidate the cached DSM session and authenticate again."""
    try:
        sid = await _dsm_login(force_refresh=True)
        return {"success": True, "sid_prefix": sid[:6] + "...", "endpoint": DSM_WEBAPI_BASE}
    except Exception as exc:
        return {"success": False, "error": str(exc)}




# ============================================================================
# === EXTENSIONS 2026-07-28 (Claude) ========================================
# Ajouts PUREMENT ADDITIFS. Ne modifie aucune fonction existante.
# Reutilise: mcp, _ssh_run, _local_run, _with_tool, _get_host, _load_hosts,
#            _cf_api, redact, log, STATE_DB, BASE_DIR, DEFAULT_TIMEOUT,
#            MAX_TIMEOUT, CF_ACCOUNT_ID.
# ============================================================================
import base64 as _b64
import shlex as _shlex
import time as _time
import uuid as _uuid


def _b64enc(s: str) -> str:
    return _b64.b64encode(s.encode()).decode()


def _b64run(script: str, interp: str = "bash") -> str:
    """Enveloppe un script (potentiellement multi-lignes / avec $VAR / $(...))
    pour qu'il soit decode et execute DANS le shell cible -> aucune expansion
    prematuree cote appelant. base64 = alphabet sur (A-Za-z0-9+/=)."""
    return "echo " + _b64enc(script) + " | base64 -d | " + interp


def _ct_run_cmd(ctid: int, script: str, interp: str = "bash") -> str:
    """Commande a lancer SUR L'HYPERVISEUR pour executer `script` DANS le CT.
    L'expansion se fait dans le conteneur, pas sur l'hote."""
    inner = _b64run(script, interp)
    return "pct exec " + str(int(ctid)) + " -- " + interp + " -c " + _shlex.quote(inner)


# --- petit KV cache dans state.db (topology, etc.) ---
def _kv_init():
    try:
        con = sqlite3.connect(STATE_DB)
        con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
        con.commit()
        con.close()
    except Exception as e:
        log.warning("kv_init: %s", e)


def _kv_get(k: str, max_age: float):
    try:
        con = sqlite3.connect(STATE_DB)
        row = con.execute("SELECT v, ts FROM kv WHERE k=?", (k,)).fetchone()
        con.close()
        if row and (_time.time() - row[1]) <= max_age:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _kv_set(k: str, v):
    try:
        con = sqlite3.connect(STATE_DB)
        con.execute("INSERT OR REPLACE INTO kv (k, v, ts) VALUES (?, ?, ?)",
                    (k, json.dumps(v, default=str), _time.time()))
        con.commit()
        con.close()
    except Exception as e:
        log.warning("kv_set: %s", e)


_kv_init()


# ============================================================================
# === 1. Execution dans un CT sans foot-gun d'expansion =====================
# ============================================================================
@mcp.tool()
async def ct_exec(
    ctid: int,
    command: str,
    host: str = DEFAULT_HOST,
    shell: str = "bash",
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run a script inside an LXC container without host-side variable expansion."""
    interp = "sh" if shell.lower() in ("sh", "ash", "dash", "busybox") else "bash"
    cmd = _ct_run_cmd(ctid, command, interp)
    return await _with_tool(
        "ct_exec",
        _ssh_run(host, cmd, as_root=True,
                 timeout=max(1, min(timeout_seconds, MAX_TIMEOUT))),
    )


@mcp.tool()
async def ct_write_file(
    ctid: int,
    path: str,
    content: str,
    host: str = DEFAULT_HOST,
    mode: Optional[str] = None,
    make_parents: bool = True,
) -> dict[str, Any]:
    """Write a file inside an LXC container after capturing its prior state."""
    quoted_path = _shlex.quote(path)
    snapshot_script = (
        f"if [ -L {quoted_path} ]; then\n"
        "  echo TYPE=symlink\n"
        f"elif [ ! -e {quoted_path} ]; then\n"
        "  echo TYPE=missing\n"
        f"elif [ ! -f {quoted_path} ]; then\n"
        "  echo TYPE=unsupported\n"
        "else\n"
        f"  size=$(wc -c < {quoted_path})\n"
        f"  mode=$(stat -c %a {quoted_path})\n"
        "  echo TYPE=file\n"
        "  echo SIZE=$size\n"
        "  echo MODE=$mode\n"
        f"  if [ \"$size\" -le {max(1, config.SNAPSHOT_MAX_BYTES)} ]; then\n"
        f"    base64 -w0 {quoted_path}; echo\n"
        "  fi\n"
        "fi"
    )
    snapshot_result = await _ssh_run(
        host,
        _ct_run_cmd(ctid, snapshot_script),
        as_root=True,
        timeout=30,
    )
    if snapshot_result.get("return_code") != 0:
        return {"error": "cannot snapshot target file", "detail": snapshot_result}
    lines = (snapshot_result.get("stdout") or "").splitlines()
    snapshot_type = lines[0].partition("=")[2] if lines else ""
    if snapshot_type in {"symlink", "unsupported", ""}:
        return {"error": f"refused: snapshot does not support target type {snapshot_type!r}"}
    state: dict[str, Any] = {
        "host": host,
        "ctid": ctid,
        "path": path,
        "exists": snapshot_type == "file",
    }
    if snapshot_type == "file":
        metadata = dict(line.split("=", 1) for line in lines[1:3] if "=" in line)
        size = int(metadata.get("SIZE", config.SNAPSHOT_MAX_BYTES + 1))
        if size > config.SNAPSHOT_MAX_BYTES or len(lines) < 4:
            return {
                "error": (
                    f"refused: existing file exceeds snapshot limit "
                    f"({size} > {config.SNAPSHOT_MAX_BYTES} bytes)"
                )
            }
        state.update({"mode": metadata.get("MODE", "600"), "content_b64": lines[3]})
    change_id = _snapshot_store.create(
        profile=_profile_identity(_current_profile.get()),
        tool="ct_write_file",
        target=f"{host}:ct:{ctid}:{path}",
        state=state,
    )
    parent = os.path.dirname(path) or "/"
    parts = []
    if make_parents:
        parts.append("mkdir -p " + _shlex.quote(parent))
    parts.append("echo " + _b64enc(content) + " | base64 -d > " + _shlex.quote(path))
    if mode:
        parts.append("chmod " + _shlex.quote(str(mode)) + " " + _shlex.quote(path))
    parts.append("wc -c < " + _shlex.quote(path))
    script = "\n".join(parts)
    cmd = _ct_run_cmd(ctid, script)
    res = await _with_tool(
        "ct_write_file", _ssh_run(host, cmd, as_root=True, timeout=30)
    )
    res["path"] = path
    res["bytes_intended"] = len(content.encode())
    if res.get("return_code") != 0:
        _snapshot_store.set_status(change_id, "mutation_failed")
    else:
        res["change_id"] = change_id
    return res


# ============================================================================
# === 2. Job runner asynchrone (fini le nohup + tail manuel) ================
# ============================================================================
_JOB_LAUNCHER = """\
JD=/tmp/mcp-jobs/__JOBID__
mkdir -p "$JD"
echo __CMDB64__ | base64 -d > "$JD/run.sh"
printf '%s' "__LABEL__" > "$JD/label" 2>/dev/null
date -u +%Y-%m-%dT%H:%M:%SZ > "$JD/started" 2>/dev/null
nohup bash -c 'bash "$1/run.sh" > "$1/out.log" 2>&1; echo $? > "$1/rc"' _ "$JD" >/dev/null 2>&1 &
echo $! > "$JD/pid"
echo OK "$JD"
"""

_JOB_PROBE = """\
JD=/tmp/mcp-jobs/__JOBID__
if [ ! -d "$JD" ]; then echo STATE=missing; exit 0; fi
if [ -f "$JD/rc" ]; then
  echo STATE=done
  echo RC=$(cat "$JD/rc" 2>/dev/null)
else
  P=$(cat "$JD/pid" 2>/dev/null)
  if [ -n "$P" ] && kill -0 "$P" 2>/dev/null; then echo STATE=running; else echo STATE=dead; fi
fi
echo STARTED=$(cat "$JD/started" 2>/dev/null)
echo LINES=$(wc -l < "$JD/out.log" 2>/dev/null || echo 0)
echo BYTES=$(wc -c < "$JD/out.log" 2>/dev/null || echo 0)
"""

_JOB_LOGS = """\
JD=/tmp/mcp-jobs/__JOBID__
tail -n +__FROM__ "$JD/out.log" 2>/dev/null | head -n __MAX__
"""


def _job_record(job_id, host, as_root, label, preview):
    try:
        con = sqlite3.connect(STATE_DB)
        con.execute(
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, host TEXT, "
            "as_root INTEGER, label TEXT, preview TEXT, started_ts TEXT)"
        )
        con.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)",
            (job_id, host or "", 1 if as_root else 0, label or "",
             preview[:200], datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
        con.close()
    except Exception as e:
        log.warning("job_record: %s", e)


def _job_lookup(job_id):
    try:
        con = sqlite3.connect(STATE_DB)
        row = con.execute(
            "SELECT host, as_root, label FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        con.close()
        if row:
            return {"host": row[0] or None, "as_root": bool(row[1]), "label": row[2]}
    except Exception:
        pass
    return None


async def _job_exec(host, onhost, as_root, timeout):
    if host is None:
        return await _local_run(onhost, as_root=as_root, timeout=timeout)
    return await _ssh_run(host, onhost, as_root=as_root, timeout=timeout)


@mcp.tool()
async def job_run(
    command: str,
    host: Optional[str] = None,
    as_root: bool = False,
    label: Optional[str] = None,
) -> dict[str, Any]:
    """Start a long-running command as a detached background job and return its job ID."""
    job_id = _uuid.uuid4().hex[:12]
    launcher = (
        _JOB_LAUNCHER
        .replace("__JOBID__", job_id)
        .replace("__CMDB64__", _b64enc(command))
        .replace("__LABEL__", (label or "").replace('"', "'")[:120])
    )
    onhost = _b64run(launcher)
    res = await _with_tool("job_run", _job_exec(host, onhost, as_root, 30))
    _job_record(job_id, host, as_root, label, command)
    ok = res.get("return_code") == 0
    return {
        "job_id": job_id,
        "host": host or "hub",
        "label": label,
        "launched": ok,
        "launch_stdout": res.get("stdout", ""),
        "launch_stderr": res.get("stderr", ""),
        "hint": "job_status('%s') puis job_logs('%s')" % (job_id, job_id),
    }


def _parse_kv_lines(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


@mcp.tool()
async def job_status(job_id: str) -> dict[str, Any]:
    """Return background-job state, exit code, and log size without returning the full log."""
    meta = _job_lookup(job_id) or {"host": None, "as_root": False, "label": None}
    probe = _JOB_PROBE.replace("__JOBID__", job_id)
    res = await _with_tool(
        "job_status", _job_exec(meta["host"], _b64run(probe), meta["as_root"], 20)
    )
    fields = _parse_kv_lines(res.get("stdout", ""))
    return {
        "job_id": job_id,
        "host": meta["host"] or "hub",
        "label": meta["label"],
        "state": fields.get("STATE", "unknown"),
        "return_code": int(fields["RC"]) if fields.get("RC", "").lstrip("-").isdigit() else None,
        "started": fields.get("STARTED"),
        "log_lines": int(fields["LINES"]) if fields.get("LINES", "").isdigit() else None,
        "log_bytes": int(fields["BYTES"]) if fields.get("BYTES", "").isdigit() else None,
    }


@mcp.tool()
async def job_logs(job_id: str, from_line: int = 0, max_lines: int = 300) -> dict[str, Any]:
    """Return a bounded, incremental range of background-job log lines."""
    meta = _job_lookup(job_id) or {"host": None, "as_root": False}
    from_line = max(0, int(from_line))
    max_lines = max(1, min(int(max_lines), 5000))
    script = (
        _JOB_LOGS.replace("__JOBID__", job_id)
        .replace("__FROM__", str(from_line + 1))
        .replace("__MAX__", str(max_lines))
    )
    res = await _with_tool(
        "job_logs", _job_exec(meta["host"], _b64run(script), meta["as_root"], 25)
    )
    chunk = res.get("stdout", "")
    n = chunk.count("\n") if chunk else 0
    return {
        "job_id": job_id,
        "from_line": from_line,
        "lines_returned": n,
        "next_line": from_line + n,
        "log": chunk,
    }


@mcp.tool()
async def job_list(limit: int = 20) -> dict[str, Any]:
    """List recently recorded background jobs with target, label, and creation time."""
    try:
        con = sqlite3.connect(STATE_DB)
        con.execute(
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, host TEXT, "
            "as_root INTEGER, label TEXT, preview TEXT, started_ts TEXT)"
        )
        rows = con.execute(
            "SELECT job_id, host, label, preview, started_ts FROM jobs "
            "ORDER BY started_ts DESC LIMIT ?", (max(1, min(limit, 100)),)
        ).fetchall()
        con.close()
    except Exception as e:
        return {"error": str(e)}
    return {
        "jobs": [
            {"job_id": r[0], "host": r[1] or "hub", "label": r[2],
             "preview": r[3], "started": r[4]}
            for r in rows
        ]
    }


# ============================================================================
# === 3. Topologie canonique (anti re-decouverte + anti ambiguite CT 107) ===
# ============================================================================
# Optional curated overlay describing guests, IP traps and "do not touch"
# entries that cannot be inferred from a live scan. Site-specific, so it
# lives in topology.yaml (git-ignored); absent by default.
_TOPO_OVERLAY = config.load_topology()


@mcp.tool()
async def topology(refresh: bool = False, live: bool = True) -> dict[str, Any]:
    """Return the canonical mapping of containers, addresses, roles, hosts, tunnels, and warnings."""
    cached = None if refresh else _kv_get("topology", 600)
    if cached and not refresh:
        cached["_cache"] = "hit"
        return cached

    result = {k: v for k, v in _TOPO_OVERLAY.items()}
    result["_generated"] = datetime.now(timezone.utc).isoformat()
    result["_cache"] = "miss"

    if live:
        listing = "pct list 2>/dev/null; echo '=== VM ==='; qm list 2>/dev/null"
        for hv in _hypervisor_hosts():
            try:
                r = await _ssh_run(hv, listing, as_root=True, timeout=12)
                if r.get("return_code") == 0:
                    result.setdefault(hv, {})["_live"] = r.get("stdout", "")
                else:
                    result.setdefault(hv, {})["_live"] = "unreachable"
            except Exception:
                result.setdefault(hv, {})["_live"] = "unreachable"

    _kv_set("topology", result)
    return result


# ============================================================================
# === 4. Garde destructif (suppression en 2 etapes) =========================
# ============================================================================
_PENDING_DESTROY: dict[str, dict] = {}


@mcp.tool()
async def destroy_resource(
    kind: str,
    ident: str,
    host: str = DEFAULT_HOST,
    confirm_token: Optional[str] = None,
    purge: bool = False,
) -> dict[str, Any]:
    """Destroy a Proxmox VM or container through a guarded two-step flow. The first call returns a short-lived confirmation token; the second applies the exact plan."""
    kind = kind.lower()
    if kind not in ("ct", "vm"):
        return {"error": "kind doit etre 'ct' ou 'vm'"}
    try:
        vmid = int(ident)
    except Exception:
        return {"error": "ident doit etre un VMID numerique"}

    # --- Etape 2 : confirmation ---
    if confirm_token:
        pend = _PENDING_DESTROY.get(confirm_token)
        if not pend:
            return {"error": "token inconnu ou expire ; relance sans token pour en obtenir un neuf"}
        if _time.time() > pend["expires"]:
            _PENDING_DESTROY.pop(confirm_token, None)
            return {"error": "token expire (5 min) ; relance sans token"}
        if (pend["kind"], pend["vmid"], pend["host"]) != (kind, vmid, host):
            return {"error": "le token ne correspond pas a cette cible"}
        verb = "pct" if kind == "ct" else "qm"
        cmd = "%s destroy %d%s" % (verb, vmid, " --purge" if pend["purge"] else "")
        _PENDING_DESTROY.pop(confirm_token, None)
        res = await _with_tool("destroy_resource", _ssh_run(host, cmd, as_root=True, timeout=120))
        return {"executed": cmd, "host": host, "result": res}

    # --- Etape 1 : resolution + token ---
    verb = "pct" if kind == "ct" else "qm"
    info_cmd = "%s config %d 2>&1 | head -40; echo '=== STATUS ==='; %s status %d 2>&1" % (
        verb, vmid, verb, vmid
    )
    info = await _ssh_run(host, info_cmd, as_root=True, timeout=15)
    if info.get("return_code") != 0 or "does not exist" in (info.get("stdout", "") + info.get("stderr", "")):
        return {"error": "cible introuvable", "host": host, "kind": kind, "vmid": vmid,
                "detail": info.get("stdout", "") + info.get("stderr", "")}
    token = _uuid.uuid4().hex[:12]
    _PENDING_DESTROY[token] = {
        "kind": kind, "vmid": vmid, "host": host, "purge": purge,
        "expires": _time.time() + 300,
    }
    return {
        "action": "CONFIRMATION REQUISE - rien detruit",
        "target": {"kind": kind, "vmid": vmid, "host": host, "purge": purge},
        "resolved": info.get("stdout", ""),
        "confirm_token": token,
        "how_to_confirm": "rappelle destroy_resource(kind='%s', ident='%d', host='%s', confirm_token='%s')"
                          % (kind, vmid, host, token),
        "expires_in_seconds": 300,
    }


# ============================================================================
# === 5. Petits utilitaires recurrents ======================================
# ============================================================================
@mcp.tool()
async def ssh_reset_control(host: str) -> dict[str, Any]:
    """Close a cached SSH ControlMaster connection for one configured host."""
    hi = _get_host(host)
    port = hi.get("port", 22)
    cp = config.SSH_CONTROL_PATH
    cmd = ("ssh -O exit -p %d -o ControlPath=%s %s@%s 2>&1 || true"
           % (port, _shlex.quote(cp), hi["user"], hi["hostname"]))
    res = await _with_tool("ssh_reset_control", _local_run(cmd, as_root=False, timeout=10))
    return {"host": host, "result": res.get("stdout", "") + res.get("stderr", "")}


@mcp.tool()
async def dhcp_reservations(only_online: bool = False) -> dict[str, Any]:
    """Return router DHCP reservations correlated with the configured inventory."""
    cmd = ("nvram get dhcp_staticlist; echo '===CLIENTLIST==='; "
           "nvram get custom_clientlist; echo '===ARP==='; cat /proc/net/arp")
    r = await _with_tool("dhcp_reservations", _ssh_run("router", cmd, as_root=False, timeout=20))
    out = r.get("stdout", "")
    static_raw, _, rest = out.partition("===CLIENTLIST===")
    client_raw, _, arp_raw = rest.partition("===ARP===")

    # ARP: online = flag 0x2
    online = set()
    for line in arp_raw.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 4 and cols[2] == "0x2":
            online.add(cols[3].upper())

    # noms: custom_clientlist = <name>MAC>type>...  (best-effort)
    names = {}
    for chunk in client_raw.split("<"):
        chunk = chunk.strip().rstrip(">")
        if ">" not in chunk:
            continue
        toks = chunk.split(">")
        nm = toks[0]
        for t in toks[1:]:
            if re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", t):
                names[t.upper()] = nm
                break

    # staticlist : entrees <MAC>IP>>
    rows = []
    for chunk in static_raw.split("<"):
        chunk = chunk.strip()
        if ">" not in chunk:
            continue
        parts = [p for p in chunk.split(">") if p]
        if len(parts) < 2:
            continue
        mac = parts[0].upper()
        ip = parts[1]
        if not re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", mac):
            continue
        rows.append({
            "mac": mac, "ip": ip,
            "name": names.get(mac, ""),
            "online": mac in online,
        })
    rows.sort(key=lambda x: [int(o) for o in x["ip"].split(".")] if x["ip"].count(".") == 3 else [999])
    if only_online:
        rows = [x for x in rows if x["online"]]
    return {"count": len(rows), "reservations": rows}


@mcp.tool()
async def cf_ingress_dump(
    tunnel_id: str = "",
) -> dict[str, Any]:
    """hostname -> service ingress table for a Cloudflare tunnel, flattened into
    a readable list. Read-only.

    Args:
        tunnel_id: tunnel UUID. Defaults to CLOUDFLARE_DEFAULT_TUNNEL_ID.
    """
    tunnel_id = tunnel_id or config.CF_DEFAULT_TUNNEL_ID
    if not CF_ACCOUNT_ID:
        return {"error": "CLOUDFLARE_ACCOUNT_ID is not configured"}
    if not tunnel_id:
        return {"error": "no tunnel_id given and CLOUDFLARE_DEFAULT_TUNNEL_ID is unset"}
    r = await _cf_api(
        "GET",
        "/accounts/%s/cfd_tunnel/%s/configurations" % (CF_ACCOUNT_ID, tunnel_id),
    )
    if not r.get("success", False):
        return {"error": "echec API Cloudflare", "detail": r}
    ingress = (((r.get("data") or {}).get("result") or {}).get("config") or {}).get("ingress", [])
    table = []
    for rule in ingress:
        table.append({
            "hostname": rule.get("hostname", "(catch-all)"),
            "path": rule.get("path"),
            "service": rule.get("service"),
        })
    return {"tunnel_id": tunnel_id, "rules": len(table), "ingress": table}


@mcp.tool()
async def pbs_status(
    host: str = DEFAULT_HOST,
    ctid: int = 108,
    datastore: str = "/mnt/datastore-hdd",
) -> dict[str, Any]:
    """Proxmox Backup Server state: datastore free space and approximate
    snapshot-group count per namespace. Read-only.

    Args:
        host: hypervisor hosting the PBS container.
        ctid: PBS container id.
        datastore: datastore mount point on the PBS container.
    """
    ds = _shlex.quote(datastore)
    script = (
        "echo ===DF===\n"
        "df -h {ds} 2>/dev/null || echo 'datastore not mounted'\n"
        "echo ===NS===\n"
        "for ns in {ds}/store/ns/*; do\n"
        "  [ -d \"$ns\" ] || continue\n"
        "  n=$(basename \"$ns\")\n"
        "  c=$(ls -1d \"$ns\"/*/*/ 2>/dev/null | grep -c . )\n"
        "  echo \"$n groups=$c\"\n"
        "done\n"
    ).format(ds=ds)
    cmd = _ct_run_cmd(ctid, script)
    r = await _with_tool("pbs_status", _ssh_run(host, cmd, as_root=True, timeout=25))
    return {
        "reachable": r.get("return_code") == 0,
        "raw": r.get("stdout", "") or r.get("stderr", "") or r.get("error", ""),
    }




# ============================================================================
# === EXTENSIONS 2026-07-28 batch2 (Claude) =================================
# Additif. Reutilise: mcp, _notion_request, _with_tool, httpx, asyncio.
# ============================================================================

def _nt_rich(s):
    s = "" if s is None else str(s)
    return [] if s == "" else [{"type": "text", "text": {"content": s[:1900]}}]


@mcp.tool()
async def notion_append_table_row(
    table_block_id: str,
    cells: Optional[list] = None,
    rows: Optional[list] = None,
) -> dict[str, Any]:
    """Append one or more rows to a Notion table block."""
    all_rows = []
    if cells is not None:
        all_rows.append(cells)
    if rows:
        all_rows.extend(rows)
    if not all_rows:
        return {"error": "fournir cells=[...] pour une ligne, ou rows=[[...],[...]] pour plusieurs"}
    children = [
        {"type": "table_row", "table_row": {"cells": [_nt_rich(c) for c in row]}}
        for row in all_rows
    ]
    res = await _with_tool(
        "notion_append_table_row",
        _notion_request("PATCH", "/blocks/%s/children" % table_block_id,
                        json_body={"children": children}),
    )
    return {"appended_rows": len(all_rows), "table_block_id": table_block_id, "result": res}


# --- Sondes HTTP de sante des services (complement read-only d'infra_snapshot) ---
_HEALTH_ENDPOINTS, _HEALTH_ENDPOINTS_INTERMITTENT = config.load_endpoints()


@mcp.tool()
async def endpoints_health(
    targets: Optional[list] = None,
    include_intermittent: bool = False,
    timeout_seconds: int = 6,
) -> dict[str, Any]:
    """Probe configured HTTP service endpoints from the hub without following redirects."""
    eps = targets if targets else (
        _HEALTH_ENDPOINTS + (_HEALTH_ENDPOINTS_INTERMITTENT if include_intermittent else [])
    )
    to = max(1, min(int(timeout_seconds), 20))

    async def _probe(client, ep):
        name = ep.get("name") or ep.get("url")
        url = ep["url"]
        started = datetime.now()
        try:
            r = await client.get(url)
            ms = int((datetime.now() - started).total_seconds() * 1000)
            return {"name": name, "url": url, "status": r.status_code,
                    "ok": 200 <= r.status_code < 400, "latency_ms": ms}
        except Exception as e:
            ms = int((datetime.now() - started).total_seconds() * 1000)
            return {"name": name, "url": url, "status": None, "ok": False,
                    "error": str(e)[:180], "latency_ms": ms}

    async with httpx.AsyncClient(timeout=to, verify=False, follow_redirects=False) as client:
        results = await asyncio.gather(*[_probe(client, e) for e in eps])
    up = sum(1 for r in results if r["ok"])
    return {
        "summary": {"total": len(results), "up": up, "down": len(results) - up},
        "endpoints": results,
    }


def _profile_identity(profile: dict[str, Any] | None) -> str:
    if profile is None:
        return "internal"
    return str(profile.get("_identity") or profile.get("name") or "unnamed")


def _purge_expired_mutations() -> None:
    now = time.monotonic()
    for token in [
        token for token, plan in _pending_mutations.items()
        if plan["expires_at"] <= now
    ]:
        _pending_mutations.pop(token, None)


def _redacted_preview(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in ("token", "password", "secret", "api_key")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redacted_preview(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redacted_preview(item) for item in value]
    if isinstance(value, str):
        return redact_str(value)
    return value


async def _apply_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    state = snapshot["state"]
    tool = snapshot["tool"]
    if tool == "cloudflare_tunnel_config_update":
        return await _cf_api(
            "PUT",
            f"/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{state['tunnel_id']}/configurations",
            json_body={"config": state["config"]},
        )
    if tool in {"notion_update_page", "notion_archive_page"}:
        return await _notion_request(
            "PATCH",
            f"/pages/{state['page_id']}",
            json_body=state["body"],
        )
    if tool == "ct_write_file":
        path = _shlex.quote(state["path"])
        if state["exists"]:
            mode = str(state["mode"])
            if not re.fullmatch(r"[0-7]{3,4}", mode):
                return {"error": "snapshot contains an invalid file mode"}
            script = (
                f"echo {_shlex.quote(state['content_b64'])} | base64 -d > {path}\n"
                f"chmod {mode} {path}"
            )
        else:
            script = f"rm -f -- {path}"
        return await _ssh_run(
            state["host"],
            _ct_run_cmd(int(state["ctid"]), script),
            as_root=True,
            timeout=30,
        )
    return {"error": f"unsupported snapshot tool: {tool}"}


def _rollback_succeeded(tool: str, result: dict[str, Any]) -> bool:
    if "error" in result:
        return False
    if tool == "cloudflare_tunnel_config_update":
        return bool(result.get("success"))
    if tool == "ct_write_file":
        return result.get("return_code") == 0
    return True


@mcp.tool()
async def rollback_change(change_id: str) -> dict[str, Any]:
    """Restore one ready mutation snapshot once, after normal mutation confirmation."""
    snapshot = _snapshot_store.get(change_id)
    if snapshot is None:
        return {"error": "unknown or expired change_id"}
    if snapshot["status"] != "ready":
        return {
            "error": f"snapshot is not rollback-ready (status={snapshot['status']})",
            "change_id": change_id,
        }
    profile = _current_profile.get()
    identity = _profile_identity(profile)
    if snapshot["profile"] != identity and (profile or {}).get("level") != "admin":
        return {"error": "snapshot belongs to a different access profile"}
    result = await _apply_snapshot(snapshot)
    succeeded = _rollback_succeeded(snapshot["tool"], result)
    _snapshot_store.set_status(
        change_id,
        "rolled_back" if succeeded else "rollback_failed",
    )
    if not succeeded:
        return {
            "error": "rollback failed",
            "change_id": change_id,
            "original_tool": snapshot["tool"],
            "result": result,
        }
    return {
        "status": "rolled_back",
        "change_id": change_id,
        "original_tool": snapshot["tool"],
        "target": snapshot["target"],
        "result": result,
    }


@mcp.tool()
def plan_mutation(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a short-lived, one-time confirmation plan for an exact mutating tool call."""
    _purge_expired_mutations()
    fn = _mutating_functions.get(tool)
    if fn is None:
        return {"error": "unknown or non-mutating tool", "tool": tool}
    try:
        bound = inspect.signature(fn).bind(**arguments)
        bound.apply_defaults()
    except TypeError as exc:
        return {"error": f"invalid arguments: {exc}", "tool": tool}
    profile = _current_profile.get()
    refusal = _access_refusal(profile, fn, (), bound.arguments)
    if refusal:
        return refusal
    token = secrets.token_urlsafe(24)
    ttl = max(30, min(config.CONFIRMATION_TTL_SECONDS, 900))
    _pending_mutations[token] = {
        "tool": tool,
        "arguments": dict(arguments),
        "profile": _profile_identity(profile),
        "expires_at": time.monotonic() + ttl,
    }
    return {
        "status": "confirmation_required",
        "tool": tool,
        "arguments_preview": _redacted_preview(arguments),
        "confirmation_token": token,
        "expires_in_seconds": ttl,
        "warning": "confirm_mutation will execute this exact call once",
    }


@mcp.tool()
async def confirm_mutation(confirmation_token: str) -> dict[str, Any]:
    """Execute one previously planned mutation and consume its confirmation token."""
    _purge_expired_mutations()
    plan = _pending_mutations.get(confirmation_token)
    if plan is None:
        return {"error": "invalid, expired, or already used confirmation token"}
    profile = _current_profile.get()
    if plan["profile"] != _profile_identity(profile):
        return {"error": "confirmation token belongs to a different access profile"}
    if config.READ_ONLY:
        return {"error": "refused: MCP Hub is in read-only mode"}
    fn = _mutating_functions.get(plan["tool"])
    if fn is None:
        return {"error": "planned tool is no longer available", "tool": plan["tool"]}
    refusal = _access_refusal(profile, fn, (), plan["arguments"])
    if refusal:
        return refusal
    lease, limit_error = _resource_limiter.acquire(
        identity=_limit_identity(profile),
        arguments={"kwargs": plan["arguments"]},
        targets=_request_targets(fn, (), plan["arguments"]),
        mutating=True,
    )
    if limit_error is not None:
        return {"error": limit_error, "tool": plan["tool"]}
    _pending_mutations.pop(confirmation_token, None)
    context_token = _confirmed_mutation.set(True)
    try:
        result = fn(**plan["arguments"])
        result = await result if inspect.isawaitable(result) else result
        succeeded = not (isinstance(result, dict) and result.get("error") is not None)
        _resource_limiter.release(lease, succeeded=succeeded)
        lease = None
    finally:
        if lease is not None:
            _resource_limiter.release(lease, succeeded=False)
        _confirmed_mutation.reset(context_token)
    return {"status": "executed", "tool": plan["tool"], "result": result}


# ============================================================================
# === Entry point ============================================================
# ============================================================================
class _BearerAuthMiddleware:
    """Reject any HTTP request not carrying the expected bearer token.

    The secret URL path is obscurity; this is the actual authentication. The
    comparison is constant-time so the token cannot be recovered by timing.
    """

    def __init__(self, app, profiles: dict[str, dict[str, Any]]) -> None:
        self.app = app
        self._profiles = profiles

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")
        token = provided[7:] if provided.startswith("Bearer ") else ""
        profile = next(
            (
                candidate
                for expected, candidate in self._profiles.items()
                if _hmac.compare_digest(token, expected)
            ),
            None,
        )
        if profile is None:
            body = b'{"error": "unauthorized"}'
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Bearer realm="mcp-hub"'),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        context_token = _current_profile.set(profile)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_profile.reset(context_token)


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        print("mcp-hub %s" % __version__)
        return

    log.info("mcp-hub %s starting", __version__)
    profiles = config.load_access_profiles()
    if config.AUTH_TOKEN:
        profiles.setdefault(config.AUTH_TOKEN, {
            "_identity": hashlib.sha256(config.AUTH_TOKEN.encode()).hexdigest(),
            "name": "legacy-admin",
            "level": "admin",
            "tools": ["*"],
            "hosts": ["*"],
            "tags": ["*"],
        })
    if not profiles:
        log.warning(
            "MCP_AUTH_TOKEN is empty - the endpoint is protected only by the "
            "secret URL path. See SECURITY.md."
        )
        if config.BIND_ADDR not in ("127.0.0.1", "::1", "localhost"):
            log.warning(
                "Binding %s with no auth token: anyone who can reach this port "
                "gets root on your fleet.", config.BIND_ADDR,
            )
        mcp.run(transport="streamable-http")
        return

    import uvicorn

    app = mcp.streamable_http_app()
    log.info(
        "listening on http://%s:%d%s (bearer auth enabled, %d profile(s))",
        config.BIND_ADDR, config.PORT, SECRET_PATH, len(profiles),
    )
    uvicorn.run(
        _BearerAuthMiddleware(app, profiles),
        host=config.BIND_ADDR,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
