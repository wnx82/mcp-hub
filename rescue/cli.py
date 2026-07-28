"""Local read-only command line interface for MCP Hub Rescue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .diagnose import diagnose_startup, recent_logs, validate_config
from .health import (
    DEFAULT_ENV_FILE,
    DEFAULT_HUB_HOME,
    DEFAULT_SERVICE,
    get_status,
    health_check,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-hub-rescue",
        description="Read-only diagnostics for a broken MCP Hub installation.",
    )
    parser.add_argument(
        "--hub-home",
        type=Path,
        default=DEFAULT_HUB_HOME,
        help=f"MCP Hub installation directory (default: {DEFAULT_HUB_HOME})",
    )
    parser.add_argument(
        "--service",
        default=DEFAULT_SERVICE,
        help=f"systemd service to inspect (default: {DEFAULT_SERVICE})",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"systemd environment file (default: {DEFAULT_ENV_FILE})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show service, version, and endpoint status.")
    commands.add_parser("health", help="Run lightweight health checks.")
    commands.add_parser("validate-config", help="Validate local configuration without importing MCP Hub.")
    commands.add_parser("diagnose", help="Diagnose startup failures without changing anything.")
    commands.add_parser("doctor", help="Alias for diagnose.")
    logs = commands.add_parser("logs", help="Read a bounded tail of MCP Hub logs.")
    logs.add_argument("--lines", type=int, default=50, help="Number of lines (maximum: 200).")
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "status":
        return get_status(args.hub_home, args.service, args.env_file)
    if args.command == "health":
        return health_check(args.hub_home, args.service, args.env_file)
    if args.command == "validate-config":
        return validate_config(args.hub_home, args.env_file)
    if args.command == "logs":
        return recent_logs(args.service, args.lines, args.hub_home)
    return diagnose_startup(args.hub_home, args.service, args.env_file)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = execute(args)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
