from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.limits import ResourceLimiter

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
            mock.patch.object(
                server,
                "_resource_limiter",
                ResourceLimiter(
                    requests_per_minute=120,
                    max_argument_bytes=200_000,
                    max_concurrent_per_target=4,
                    circuit_failures=3,
                    circuit_reset_seconds=60,
                    mutation_cooldown_seconds=0,
                ),
            ),
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

    def test_sensitive_mutation_confirmation_token_is_persisted(self) -> None:
        calls = []

        async def sample_mutation(value: str) -> dict:
            calls.append(value)
            return {"value": value}

        with (
            mock.patch.object(config, "READ_ONLY", False),
            mock.patch.dict(server._mutating_functions, {"sample_mutation": sample_mutation}),
            mock.patch.object(
                server,
                "_resource_limiter",
                ResourceLimiter(
                    requests_per_minute=120,
                    max_argument_bytes=200_000,
                    max_concurrent_per_target=4,
                    circuit_failures=3,
                    circuit_reset_seconds=60,
                    mutation_cooldown_seconds=0,
                ),
            ),
        ):
            plan = asyncio.run(server.plan_mutation("sample_mutation", {"value": "persisted"}))
            token = plan["data"]["confirmation_token"]
            original_store = server._pending_store
            try:
                server._pending_store = server.PendingOperationStore(server.STATE_DB)
                result = asyncio.run(server.confirm_mutation(token))
            finally:
                server._pending_store = original_store

        self.assertEqual(["persisted"], calls)
        self.assertEqual("executed", result["data"]["status"])

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

    def test_http_request_log_fields_extract_mcp_headers(self) -> None:
        fields = server._http_request_log_fields(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "client": ("127.0.0.1", 12345),
                "headers": [
                    (b"mcp-protocol-version", b"2026-07-28"),
                    (b"mcp-method", b"tools/call"),
                    (b"mcp-name", b"list_hosts"),
                    (b"origin", b"https://example.test"),
                ],
            }
        )
        self.assertEqual("POST", fields["http_method"])
        self.assertEqual("/mcp", fields["path"])
        self.assertEqual("127.0.0.1", fields["client"])
        self.assertEqual("2026-07-28", fields["mcp_protocol_version"])
        self.assertEqual("tools/call", fields["mcp_method"])
        self.assertEqual("list_hosts", fields["mcp_name"])
        self.assertEqual("https://example.test", fields["origin"])

    def test_standardized_alias_reports_its_public_tool_name(self) -> None:
        result = asyncio.run(server.get_mcp_stats())
        self.assertTrue(result["ok"])
        self.assertEqual("get_mcp_stats", result["tool"])
        self.assertIn("stats", result["data"])

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

    def test_notion_rollback_only_restores_requested_writable_fields(self) -> None:
        current = {
            "archived": False,
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"type": "text", "text": {"content": "before"}}],
                },
                "Computed": {"type": "formula", "formula": {"number": 2}},
            },
        }
        rollback = server._notion_rollback_body(
            current,
            {
                "archived": True,
                "properties": {
                    "Name": {"title": [{"text": {"content": "after"}}]},
                },
            },
        )
        self.assertFalse(rollback["archived"])
        self.assertEqual(
            current["properties"]["Name"]["title"],
            rollback["properties"]["Name"]["title"],
        )
        self.assertNotIn("Computed", rollback["properties"])

    def test_notion_rollback_refuses_read_only_property_types(self) -> None:
        rollback = server._notion_rollback_body(
            {
                "properties": {
                    "Computed": {"type": "formula", "formula": {"number": 2}},
                }
            },
            {"properties": {"Computed": {"formula": {"number": 3}}}},
        )
        self.assertIn("cannot be snapshotted safely", rollback["error"])

    def test_rollback_change_consumes_snapshot_once(self) -> None:
        from core.snapshots import SnapshotStore

        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory) / "state.db")
            change_id = store.create(
                profile="internal",
                tool="notion_archive_page",
                target="notion:page:example",
                state={"page_id": "example", "body": {"archived": False}},
            )
            with (
                mock.patch.object(server, "_snapshot_store", store),
                mock.patch.object(
                    server,
                    "_apply_snapshot",
                    new=mock.AsyncMock(return_value={"archived": False}),
                ) as apply_snapshot,
            ):
                result = asyncio.run(server.rollback_change.__wrapped__(change_id))
                replay = asyncio.run(server.rollback_change.__wrapped__(change_id))

        self.assertEqual("rolled_back", result["status"])
        self.assertIn("not rollback-ready", replay["error"])
        apply_snapshot.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
