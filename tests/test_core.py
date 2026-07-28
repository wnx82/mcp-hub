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
