from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HUB_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("mcp", "httpx", "yaml")
)


@unittest.skipUnless(HUB_DEPENDENCIES_AVAILABLE, "MCP Hub dependencies are not installed")
class CoreHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["MCP_READ_ONLY"] = "true"
        global config, server
        import config
        import server

    def test_redacts_common_secret_shapes(self) -> None:
        text = (
            b"token=super-secret password=hunter2 "
            b"api_key=abcdef secret=value "
            b"AKIAABCDEFGHIJKLMNOP"
        )
        result = server.redact(text)
        self.assertNotIn("super-secret", result)
        self.assertNotIn("hunter2", result)
        self.assertNotIn("abcdef", result)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", result)
        self.assertGreaterEqual(result.count("[REDACTED"), 4)

    def test_read_only_guard_refuses_mutating_tool(self) -> None:
        result = asyncio.run(
            server.mcp.call_tool("local_exec", {"command": "echo must-not-run"})
        )
        self.assertIn("read-only", str(result))
        self.assertIn("local_exec", str(result))

    def test_typed_environment_helpers(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TEST_BOOL": "yes", "TEST_INT": "42", "TEST_BAD_INT": "invalid"},
            clear=False,
        ):
            self.assertTrue(config.env_bool("TEST_BOOL"))
            self.assertEqual(42, config.env_int("TEST_INT", 1))
            self.assertEqual(7, config.env_int("TEST_BAD_INT", 7))

    def test_access_profile_loading_and_target_restrictions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(
                json.dumps({
                    "tokens": {
                        "test-token": {
                            "name": "operator",
                            "level": "operate",
                            "tools": ["remote_exec"],
                            "hosts": ["storage"],
                        }
                    }
                }),
                encoding="utf-8",
            )
            with mock.patch.object(config, "AUTH_PROFILES_FILE", path):
                profile = config.load_access_profiles()["test-token"]

        def remote_exec(host: str) -> None:
            return None

        self.assertIsNone(server._access_refusal(profile, remote_exec, (), {"host": "storage"}))
        refused = server._access_refusal(profile, remote_exec, (), {"host": "hypervisor"})
        self.assertIn("target", refused["error"])

    def test_read_profile_cannot_call_mutating_tool(self) -> None:
        profile = {
            "name": "reader",
            "level": "read",
            "tools": ["*"],
            "hosts": ["*"],
            "tags": [],
        }

        def remote_exec(host: str) -> None:
            return None

        refused = server._access_refusal(profile, remote_exec, (), {"host": "storage"})
        self.assertIn("read-only", refused["error"])

    def test_sensitive_mutation_requires_one_time_confirmation(self) -> None:
        calls = []

        async def sample_mutation(value: str, api_token: str = "") -> dict:
            calls.append(value)
            return {"value": value}

        with (
            mock.patch.object(config, "READ_ONLY", False),
            mock.patch.dict(server._mutating_functions, {"sample_mutation": sample_mutation}),
        ):
            plan = asyncio.run(
                server.plan_mutation(
                    "sample_mutation",
                    {"value": "expected", "api_token": "must-not-leak"},
                )
            )
            token = plan["data"]["confirmation_token"]
            self.assertEqual(
                "[REDACTED]", plan["data"]["arguments_preview"]["api_token"]
            )
            result = asyncio.run(server.confirm_mutation(token))
            replay = asyncio.run(server.confirm_mutation(token))

        self.assertEqual(["expected"], calls)
        self.assertEqual("executed", result["data"]["status"])
        self.assertIn("already used", replay["error"])

    def test_direct_sensitive_mutation_is_refused_before_execution(self) -> None:
        with (
            mock.patch.object(config, "READ_ONLY", False),
            mock.patch.object(config, "CONFIRMATION_MODE", "sensitive"),
        ):
            result = asyncio.run(
                server.service_ctl("example", "restart", host=None)
            )
        self.assertIn("confirmation plan", result["error"])

    def test_tool_response_has_common_envelope(self) -> None:
        result = asyncio.run(server.list_hosts())
        self.assertEqual(
            {"ok", "data", "error", "duration_ms", "host", "request_id", "tool"},
            set(result),
        )
        self.assertTrue(result["ok"])
        self.assertEqual("list_hosts", result["tool"])
        self.assertEqual(24, len(result["request_id"]))

    def test_audit_export_correlates_tool_calls(self) -> None:
        call = asyncio.run(server.list_hosts())
        exported = asyncio.run(server.audit_export(last_n=20))
        entries = exported["data"]["entries"]
        matching = [
            entry for entry in entries if entry["request_id"] == call["request_id"]
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual("list_hosts", matching[0]["tool"])
        self.assertTrue(matching[0]["ok"])

    def test_yaml_inventory_loaders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hosts = root / "hosts.yaml"
            topology = root / "topology.yaml"
            endpoints = root / "endpoints.yaml"
            hosts.write_text(
                "hosts:\n"
                "  example:\n"
                "    hostname: host.example.com\n"
                "    user: operator\n",
                encoding="utf-8",
            )
            topology.write_text("guests:\n  '100': example\n", encoding="utf-8")
            endpoints.write_text(
                "endpoints:\n"
                "  - name: example\n"
                "    url: https://service.example.com/health\n"
                "intermittent: []\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(config, "HOSTS_FILE", hosts),
                mock.patch.object(config, "TOPOLOGY_FILE", topology),
                mock.patch.object(config, "ENDPOINTS_FILE", endpoints),
            ):
                self.assertEqual("operator", config.load_hosts()["example"]["user"])
                self.assertEqual("example", config.load_topology()["guests"]["100"])
                always, intermittent = config.load_endpoints()
                self.assertEqual("example", always[0]["name"])
                self.assertEqual([], intermittent)


if __name__ == "__main__":
    unittest.main()
