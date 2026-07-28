"""Configuration, log, and startup diagnostics for MCP Hub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .health import (
    DEFAULT_ENV_FILE,
    DEFAULT_HUB_HOME,
    DEFAULT_SERVICE,
    get_status,
    health_check,
    new_request_id,
    parse_env_file,
    run_command,
    utc_now,
)

MAX_LOG_LINES = 200
MAX_LOG_BYTES = 100_000

_YAML_CHECK = (
    "import importlib,json,sys\n"
    "try:\n"
    " yaml=importlib.import_module('yaml')\n"
    " with open(sys.argv[1],encoding='utf-8') as fh: data=yaml.safe_load(fh)\n"
    " print(json.dumps({'ok':True,'data':data},default=str))\n"
    "except Exception as exc:\n"
    " mark=getattr(exc,'problem_mark',None)\n"
    " print(json.dumps({'ok':False,'error':str(exc),"
    "'line':getattr(mark,'line',-1)+1 if mark else None}))\n"
    " sys.exit(1)\n"
)


def _tail_file(path: Path, lines: int) -> list[str]:
    """Read a bounded tail without loading an entire log file."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            remaining = handle.tell()
            chunks: list[bytes] = []
            newlines = 0
            while remaining > 0 and newlines <= lines and sum(map(len, chunks)) < MAX_LOG_BYTES:
                size = min(4096, remaining)
                remaining -= size
                handle.seek(remaining)
                chunk = handle.read(size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
        return b"".join(reversed(chunks)).decode(errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def recent_logs(
    service: str = DEFAULT_SERVICE,
    lines: int = 50,
    hub_home: Path = DEFAULT_HUB_HOME,
) -> dict[str, Any]:
    limit = max(1, min(int(lines), MAX_LOG_LINES))
    result = run_command([
        "journalctl",
        "--unit",
        service,
        "--lines",
        str(limit),
        "--no-pager",
        "--output",
        "short-iso",
    ])
    output = result["stdout"].splitlines()[-limit:] if result["stdout"] else []
    source = "journal"
    if not output:
        output = _tail_file(hub_home / "mcp-hub.log", limit)
        source = "file"
    return {
        "ok": bool(output) or result["ok"],
        "request_id": new_request_id(),
        "timestamp": utc_now(),
        "source": source,
        "lines": output,
        "line_count": len(output),
        "limit": limit,
        "error": None if output else (result["stderr"] or None),
    }


def _validate_yaml(path: Path, required: bool, hub_home: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "ok": not required,
            "file": str(path),
            "required": required,
            "status": "missing",
            "line": None,
            "problem": "required file is missing" if required else None,
        }
    if not path.is_file():
        return {
            "ok": False,
            "file": str(path),
            "required": required,
            "status": "invalid",
            "line": None,
            "problem": "path is not a regular file",
        }

    python = hub_home / ".venv" / "bin" / "python"
    if not python.is_file():
        return {
            "ok": False,
            "file": str(path),
            "required": required,
            "status": "unverified",
            "line": None,
            "problem": "cannot validate YAML: hub virtualenv Python is missing",
        }
    result = run_command([str(python), "-I", "-c", _YAML_CHECK, str(path)], timeout=10)
    try:
        payload = json.loads(result["stdout"])
    except (json.JSONDecodeError, TypeError):
        payload = {
            "ok": False,
            "error": result["stderr"] or "YAML validator returned no result",
            "line": None,
        }
    if not payload.get("ok"):
        return {
            "ok": False,
            "file": str(path),
            "required": required,
            "status": "invalid",
            "line": payload.get("line"),
            "problem": payload.get("error"),
        }

    data = payload.get("data")
    problem = None
    name = path.name
    if data is not None and not isinstance(data, dict):
        problem = "top-level YAML value must be a mapping"
    elif name == "hosts.yaml" and not isinstance((data or {}).get("hosts"), dict):
        problem = "hosts.yaml must contain a 'hosts' mapping"
    elif name == "endpoints.yaml":
        for key in ("endpoints", "intermittent"):
            if key in (data or {}) and not isinstance(data[key], list):
                problem = f"'{key}' must be a list"
                break
    return {
        "ok": problem is None,
        "file": str(path),
        "required": required,
        "status": "valid" if problem is None else "invalid",
        "line": None,
        "problem": problem,
    }


def _validate_environment(path: Path, required: bool) -> dict[str, Any]:
    if not path.exists():
        return {
            "ok": not required,
            "file": str(path),
            "required": required,
            "status": "missing",
            "problems": [],
        }
    _, problems = parse_env_file(path)
    return {
        "ok": not problems,
        "file": str(path),
        "required": required,
        "status": "valid" if not problems else "invalid",
        "problems": problems,
    }


def validate_config(
    hub_home: Path = DEFAULT_HUB_HOME,
    env_file: Path = DEFAULT_ENV_FILE,
) -> dict[str, Any]:
    files = [
        _validate_environment(env_file, required=False),
        _validate_environment(hub_home / ".env", required=False),
        _validate_yaml(hub_home / "hosts.yaml", required=True, hub_home=hub_home),
        _validate_yaml(hub_home / "topology.yaml", required=False, hub_home=hub_home),
        _validate_yaml(hub_home / "endpoints.yaml", required=False, hub_home=hub_home),
    ]
    return {
        "ok": all(item["ok"] for item in files),
        "request_id": new_request_id(),
        "timestamp": utc_now(),
        "files": files,
    }


def diagnose_startup(
    hub_home: Path = DEFAULT_HUB_HOME,
    service: str = DEFAULT_SERVICE,
    env_file: Path = DEFAULT_ENV_FILE,
    log_lines: int = 50,
) -> dict[str, Any]:
    status = get_status(hub_home, service, env_file)
    health = health_check(hub_home, service, env_file)
    configuration = validate_config(hub_home, env_file)
    logs = recent_logs(service, log_lines, hub_home)
    findings: list[dict[str, str]] = []

    if health["checks"]["service"]["active_state"] != "active":
        findings.append({
            "severity": "critical",
            "area": "service",
            "problem": "mcp-hub service is not active",
            "suggestion": "inspect the recent logs before attempting a restart",
        })
    if not health["checks"]["imports"]["ok"]:
        findings.append({
            "severity": "critical",
            "area": "dependencies",
            "problem": health["checks"]["imports"]["error"] or "essential import failed",
            "suggestion": "repair the hub virtualenv or roll back to the last known good version",
        })
    if not configuration["ok"]:
        findings.append({
            "severity": "critical",
            "area": "configuration",
            "problem": "one or more configuration files are invalid or unavailable",
            "suggestion": "review validate-config details; do not overwrite local configuration",
        })
    if not health["checks"]["endpoint"]["ok"]:
        findings.append({
            "severity": "critical",
            "area": "endpoint",
            "problem": health["checks"]["endpoint"]["error"] or "MCP endpoint is unavailable",
            "suggestion": "verify the service state, bind address, port, and startup logs",
        })
    if not health["checks"]["disk"]["ok"]:
        findings.append({
            "severity": "warning",
            "area": "disk",
            "problem": "free disk space is below the configured minimum",
            "suggestion": "free disk space before updating or repairing dependencies",
        })

    return {
        "ok": not any(item["severity"] == "critical" for item in findings),
        "request_id": new_request_id(),
        "timestamp": utc_now(),
        "status": status,
        "health": health,
        "configuration": configuration,
        "findings": findings,
        "recent_logs": logs,
    }
