"""Read-only health checks that remain usable when MCP Hub cannot import."""

from __future__ import annotations

import http.client
import os
import re
import shutil
import socket
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HUB_HOME = Path("/opt/mcp-hub")
DEFAULT_ENV_FILE = Path("/etc/default/mcp-hub")
DEFAULT_SERVICE = "mcp-hub.service"
DEFAULT_MIN_DISK_MB = 256
MAX_COMMAND_OUTPUT = 100_000

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_request_id() -> str:
    return uuid.uuid4().hex


def run_command(args: list[str], timeout: int = 10) -> dict[str, Any]:
    """Run one fixed command without a shell and return bounded output."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "return_code": proc.returncode,
            "stdout": proc.stdout[-MAX_COMMAND_OUTPUT:].strip(),
            "stderr": proc.stderr[-MAX_COMMAND_OUTPUT:].strip(),
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "return_code": None,
            "stdout": "",
            "stderr": f"command not found: {args[0]}",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "return_code": None,
            "stdout": "",
            "stderr": f"command timed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "ok": False,
            "return_code": None,
            "stdout": "",
            "stderr": str(exc),
        }


def parse_env_file(path: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Parse simple KEY=VALUE entries without evaluating shell expressions."""
    values: dict[str, str] = {}
    problems: list[dict[str, Any]] = []
    if not path.is_file():
        return values, problems

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return values, [{"line": None, "problem": str(exc)}]

    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            problems.append({
                "line": number,
                "problem": "expected KEY=VALUE",
            })
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            problems.append({
                "line": number,
                "problem": "invalid environment variable name",
            })
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values, problems


def resolved_environment(hub_home: Path, env_file: Path) -> dict[str, str]:
    """Resolve only the values needed for diagnostics, without exposing them."""
    local, _ = parse_env_file(hub_home / ".env")
    system, _ = parse_env_file(env_file)
    values = {**local, **system}
    for name in (
        "MCP_HUB_HOME",
        "MCP_BIND_ADDR",
        "MCP_PORT",
        "MCP_SECRET_PATH",
        "MCP_AUTH_TOKEN",
    ):
        if name in os.environ:
            values[name] = os.environ[name]
    return values


def read_version(hub_home: Path) -> dict[str, Any]:
    path = hub_home / "_version.py"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "version": None, "error": str(exc), "path": str(path)}
    match = _VERSION_RE.search(content)
    if not match:
        return {
            "ok": False,
            "version": None,
            "error": "__version__ assignment not found",
            "path": str(path),
        }
    return {"ok": True, "version": match.group(1), "error": None, "path": str(path)}


def service_status(service: str) -> dict[str, Any]:
    properties = [
        "LoadState",
        "ActiveState",
        "SubState",
        "MainPID",
        "ExecMainStatus",
        "NRestarts",
        "ActiveEnterTimestamp",
    ]
    result = run_command([
        "systemctl",
        "show",
        service,
        "--no-pager",
        f"--property={','.join(properties)}",
    ])
    parsed: dict[str, str] = {}
    for line in result["stdout"].splitlines():
        key, separator, value = line.partition("=")
        if separator:
            parsed[key] = value
    return {
        "ok": (
            result["ok"]
            and parsed.get("LoadState") == "loaded"
            and parsed.get("ActiveState") == "active"
        ),
        "service": service,
        "load_state": parsed.get("LoadState", "unknown"),
        "active_state": parsed.get("ActiveState", "unknown"),
        "sub_state": parsed.get("SubState", "unknown"),
        "pid": int(parsed.get("MainPID") or 0),
        "exit_status": int(parsed.get("ExecMainStatus") or 0),
        "restart_count": int(parsed.get("NRestarts") or 0),
        "active_since": parsed.get("ActiveEnterTimestamp") or None,
        "error": result["stderr"] or None,
    }


def probe_endpoint(hub_home: Path, env_file: Path, timeout: float = 3.0) -> dict[str, Any]:
    values = resolved_environment(hub_home, env_file)
    host = values.get("MCP_BIND_ADDR", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    try:
        port = int(values.get("MCP_PORT", "8000"))
    except ValueError:
        return {"ok": False, "host": host, "port": None, "error": "MCP_PORT is not an integer"}

    path = values.get("MCP_SECRET_PATH", "/mcp")
    if not path.startswith("/"):
        path = "/" + path
    token = values.get("MCP_AUTH_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "tcp": False,
            "http_status": None,
            "error": str(exc),
        }

    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("HEAD", path, headers=headers)
        response = connection.getresponse()
        response.read(1024)
        return {
            "ok": True,
            "host": host,
            "port": port,
            "tcp": True,
            "http_status": response.status,
            "error": None,
        }
    except OSError as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "tcp": True,
            "http_status": None,
            "error": str(exc),
        }
    finally:
        connection.close()


def check_imports(hub_home: Path) -> dict[str, Any]:
    python = hub_home / ".venv" / "bin" / "python"
    if not python.is_file():
        return {
            "ok": False,
            "python": str(python),
            "error": "hub virtualenv Python is missing",
        }
    code = (
        "import importlib\n"
        "names=('mcp','httpx','yaml','uvicorn')\n"
        "for name in names: importlib.import_module(name)\n"
    )
    result = run_command([str(python), "-I", "-c", code], timeout=15)
    return {
        "ok": result["ok"],
        "python": str(python),
        "error": result["stderr"] or None,
    }


def disk_status(hub_home: Path, minimum_mb: int = DEFAULT_MIN_DISK_MB) -> dict[str, Any]:
    target = hub_home if hub_home.exists() else hub_home.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return {"ok": False, "path": str(target), "error": str(exc)}
    free_mb = usage.free // (1024 * 1024)
    return {
        "ok": free_mb >= minimum_mb,
        "path": str(target),
        "free_mb": free_mb,
        "minimum_mb": minimum_mb,
        "error": None,
    }


def get_status(
    hub_home: Path = DEFAULT_HUB_HOME,
    service: str = DEFAULT_SERVICE,
    env_file: Path = DEFAULT_ENV_FILE,
) -> dict[str, Any]:
    service_info = service_status(service)
    return {
        "ok": service_info["active_state"] == "active",
        "request_id": new_request_id(),
        "timestamp": utc_now(),
        "hub_home": str(hub_home),
        "service": service_info,
        "version": read_version(hub_home),
        "endpoint": probe_endpoint(hub_home, env_file),
    }


def health_check(
    hub_home: Path = DEFAULT_HUB_HOME,
    service: str = DEFAULT_SERVICE,
    env_file: Path = DEFAULT_ENV_FILE,
    minimum_disk_mb: int = DEFAULT_MIN_DISK_MB,
) -> dict[str, Any]:
    checks = {
        "service": service_status(service),
        "version": read_version(hub_home),
        "endpoint": probe_endpoint(hub_home, env_file),
        "imports": check_imports(hub_home),
        "disk": disk_status(hub_home, minimum_disk_mb),
    }
    return {
        "ok": all(check["ok"] for check in checks.values()),
        "request_id": new_request_id(),
        "timestamp": utc_now(),
        "checks": checks,
    }
