from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rescue import diagnose, health
from rescue.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORTS = {"config", "httpx", "mcp", "server", "tools", "yaml", "uvicorn"}


class RescueIsolationTests(unittest.TestCase):
    def test_rescue_has_no_forbidden_imports(self) -> None:
        violations = []
        for path in sorted((REPO_ROOT / "rescue").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORTS:
                        violations.append(f"{path.name}:{node.lineno}: {name}")
        self.assertEqual([], violations)

    def test_cli_import_survives_blocked_hub_modules(self) -> None:
        code = """
import builtins
real_import = builtins.__import__
blocked = {'config', 'httpx', 'mcp', 'server', 'tools', 'yaml', 'uvicorn'}
def guarded(name, *args, **kwargs):
    if name.split('.', 1)[0] in blocked:
        raise AssertionError('forbidden import: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import rescue.cli
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)


class RescueHealthTests(unittest.TestCase):
    def test_version_is_read_without_importing_hub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub_home = Path(directory)
            (hub_home / "_version.py").write_text(
                'raise RuntimeError("must not import")\n__version__ = "9.8.7"\n',
                encoding="utf-8",
            )
            result = health.read_version(hub_home)
        self.assertTrue(result["ok"])
        self.assertEqual("9.8.7", result["version"])

    def test_environment_parser_reports_line_without_exposing_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("MCP_PORT=8000\nBROKEN LINE\nTOKEN=secret\n", encoding="utf-8")
            values, problems = health.parse_env_file(path)
        self.assertEqual("8000", values["MCP_PORT"])
        self.assertEqual(2, problems[0]["line"])
        self.assertNotIn("secret", str(problems))

    def test_missing_virtualenv_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = health.check_imports(Path(directory))
        self.assertFalse(result["ok"])
        self.assertIn("virtualenv", result["error"])

    def test_process_status_reports_uptime_for_running_pid(self) -> None:
        with mock.patch(
            "rescue.health.run_command",
            return_value={"ok": True, "return_code": 0, "stdout": "123 456 python\n", "stderr": ""},
        ):
            result = health.process_status(123)
        self.assertTrue(result["ok"])
        self.assertTrue(result["running"])
        self.assertEqual(456, result["uptime_seconds"])
        self.assertEqual("python", result["command"])

    def test_status_includes_process_endpoint_last_error_and_config(self) -> None:
        service_info = {
            "ok": True,
            "service": "mcp-hub.service",
            "load_state": "loaded",
            "active_state": "active",
            "sub_state": "running",
            "pid": 4242,
            "exit_status": 0,
            "restart_count": 1,
            "active_since": "Tue 2026-07-28 12:00:00 UTC",
            "error": None,
        }
        with (
            mock.patch("rescue.health.service_status", return_value=service_info),
            mock.patch(
                "rescue.health.process_status",
                return_value={
                    "ok": True,
                    "pid": 4242,
                    "running": True,
                    "uptime_seconds": 300,
                    "command": "python",
                    "error": None,
                },
            ),
            mock.patch(
                "rescue.health.read_version",
                return_value={"ok": True, "version": "1.2.3", "error": None, "path": "/tmp/_version.py"},
            ),
            mock.patch(
                "rescue.health.probe_endpoint",
                return_value={
                    "ok": True,
                    "host": "127.0.0.1",
                    "port": 8000,
                    "path": "/mcp",
                    "url": "http://127.0.0.1:8000/mcp",
                    "auth_configured": True,
                    "tcp": True,
                    "http_status": 401,
                    "error": None,
                },
            ),
            mock.patch(
                "rescue.health.latest_service_error",
                return_value={"ok": True, "source": "journal", "message": "last error line", "error": None},
            ),
            mock.patch(
                "rescue.diagnose.validate_config",
                return_value={"ok": True, "files": [{"file": "hosts.yaml", "status": "valid"}]},
            ),
        ):
            result = health.get_status(Path("/tmp/hub"), "mcp-hub.service", Path("/tmp/env"))
        self.assertTrue(result["ok"])
        self.assertEqual(4242, result["process"]["pid"])
        self.assertEqual(300, result["process"]["uptime_seconds"])
        self.assertEqual("http://127.0.0.1:8000/mcp", result["endpoint"]["url"])
        self.assertEqual("last error line", result["last_error"]["message"])
        self.assertTrue(result["configuration"]["ok"])

    def test_health_check_includes_all_requested_checks(self) -> None:
        service_info = {
            "ok": True,
            "service": "mcp-hub.service",
            "load_state": "loaded",
            "active_state": "active",
            "sub_state": "running",
            "pid": 4242,
            "exit_status": 0,
            "restart_count": 0,
            "active_since": "Tue 2026-07-28 12:00:00 UTC",
            "error": None,
        }
        with (
            mock.patch("rescue.health.service_status", return_value=service_info),
            mock.patch(
                "rescue.health.process_status",
                return_value={"ok": True, "pid": 4242, "running": True, "uptime_seconds": 90, "error": None},
            ),
            mock.patch("rescue.health.read_version", return_value={"ok": True, "version": "1.2.3"}),
            mock.patch("rescue.health.probe_endpoint", return_value={"ok": True, "url": "http://127.0.0.1:8000/mcp"}),
            mock.patch("rescue.health.check_imports", return_value={"ok": True}),
            mock.patch("rescue.health.disk_status", return_value={"ok": True, "free_mb": 1024}),
            mock.patch("rescue.diagnose.validate_config", return_value={"ok": True, "files": []}),
        ):
            result = health.health_check(Path("/tmp/hub"), "mcp-hub.service", Path("/tmp/env"))
        self.assertTrue(result["ok"])
        self.assertEqual(
            {"service", "process", "version", "endpoint", "imports", "configuration", "disk"},
            set(result["checks"]),
        )


class RescueDiagnosticsTests(unittest.TestCase):
    def test_log_limit_is_capped(self) -> None:
        command_result = {
            "ok": True,
            "return_code": 0,
            "stdout": "\n".join(str(index) for index in range(300)),
            "stderr": "",
        }
        with mock.patch("rescue.diagnose.run_command", return_value=command_result):
            result = diagnose.recent_logs(lines=500)
        self.assertEqual(diagnose.MAX_LOG_LINES, result["limit"])
        self.assertEqual(diagnose.MAX_LOG_LINES, result["line_count"])

    def test_invalid_environment_is_in_config_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hub_home = Path(directory)
            env_file = hub_home / "system.env"
            env_file.write_text("NOT AN ASSIGNMENT\n", encoding="utf-8")
            result = diagnose.validate_config(hub_home, env_file)
        self.assertFalse(result["ok"])
        environment = result["files"][0]
        self.assertEqual("invalid", environment["status"])
        self.assertEqual(1, environment["problems"][0]["line"])

    def test_cli_exposes_read_only_commands_only(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        for command in ("status", "health", "logs", "validate-config", "diagnose", "doctor"):
            self.assertIn(command, help_text)
        for command in ("restart", "repair", "rollback", "shell"):
            self.assertNotIn(command, help_text)


if __name__ == "__main__":
    unittest.main()
