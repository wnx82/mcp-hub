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
