from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args, get_origin
from unittest import mock

HUB_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("mcp", "httpx", "yaml")
)


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers = {"server": "fake-http"}

    def json(self) -> dict[str, Any]:
        return {"data": []}


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, *args, **kwargs) -> _FakeResponse:
        return _FakeResponse(status_code=503)


@unittest.skipUnless(HUB_DEPENDENCIES_AVAILABLE, "MCP Hub dependencies are not installed")
class ReadOnlyToolSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["MCP_READ_ONLY"] = "true"
        global server
        import server

    def _required_arguments_for_tool(self, tool_name: str) -> dict[str, Any]:
        if tool_name == "plan_mutation":
            return {"tool": "local_exec", "arguments": {"command": "echo test"}}

        fn = getattr(server, tool_name)
        args: dict[str, Any] = {}
        for parameter in inspect.signature(fn).parameters.values():
            if parameter.default is not inspect._empty:
                continue
            args[parameter.name] = self._sample_value(parameter.name, parameter.annotation)
        return args

    def _sample_value(self, name: str, annotation: Any) -> Any:
        if name in {"path"}:
            return str(Path("README.md"))
        if name in {"host"}:
            return "demo-host"
        if name in {"endpoint"}:
            return "demo-endpoint"
        if name in {"service"}:
            return "demo.service"
        if name in {"field_name"}:
            return "username"
        if name in {"query", "pattern"}:
            return "example"
        if name in {"folder_path"}:
            return "/volume1/example"
        if name in {"item_ref", "name_or_id"}:
            return "example-item"
        if name in {"page_id", "block_id", "database_id", "tunnel_id", "job_id", "id"}:
            return "example-id"
        if name in {"ctid"}:
            return 100
        if name in {"targets"}:
            return []

        origin = get_origin(annotation)
        if origin is not None:
            if origin is list:
                return []
            if origin is dict:
                return {}
            for inner in get_args(annotation):
                if inner is type(None):
                    continue
                return self._sample_value(name, inner)

        if annotation in {str, "str"}:
            return "example"
        if annotation in {int, "int"}:
            return 1
        if annotation in {bool, "bool"}:
            return True
        if annotation in {dict, "dict"}:
            return {}
        if annotation in {list, "list"}:
            return []
        return "example"

    def test_every_registered_read_only_tool_returns_a_structured_result(self) -> None:
        async def fake_local_run(*args, **kwargs) -> dict[str, Any]:
            return {"return_code": 1, "stdout": "", "stderr": "simulated unavailable"}

        async def fake_ssh_run(*args, **kwargs) -> dict[str, Any]:
            return {"return_code": 1, "stdout": "", "stderr": "simulated unavailable"}

        async def fake_cf_api(*args, **kwargs) -> dict[str, Any]:
            return {"success": False, "error": "simulated unavailable"}

        async def fake_bw_request(*args, **kwargs) -> dict[str, Any]:
            return {"error": "simulated unavailable"}

        async def fake_notion_request(*args, **kwargs) -> dict[str, Any]:
            return {"error": "simulated unavailable"}

        async def fake_n8n_request(*args, **kwargs) -> dict[str, Any]:
            return {"success": False, "http_status": 503, "error": "simulated unavailable"}

        async def fake_dsm_call(*args, **kwargs) -> dict[str, Any]:
            return {"error": "simulated unavailable"}

        async def fake_open_connection(*args, **kwargs):
            raise OSError("simulated unavailable")

        with (
            mock.patch.object(server, "_load_hosts", return_value={}),
            mock.patch.object(
                server,
                "_get_host",
                side_effect=lambda name: {"hostname": "127.0.0.1", "user": "root", "port": 22},
            ),
            mock.patch.object(server, "_hypervisor_hosts", return_value=[]),
            mock.patch.object(server, "_local_run", side_effect=fake_local_run),
            mock.patch.object(server, "_ssh_run", side_effect=fake_ssh_run),
            mock.patch.object(server, "_cf_api", side_effect=fake_cf_api),
            mock.patch.object(server, "_bw_request", side_effect=fake_bw_request),
            mock.patch.object(server, "_notion_request", side_effect=fake_notion_request),
            mock.patch.object(server, "_n8n_request", side_effect=fake_n8n_request),
            mock.patch.object(server, "_dsm_call", side_effect=fake_dsm_call),
            mock.patch.object(server, "_tcp_open", return_value=False),
            mock.patch.object(server.asyncio, "open_connection", side_effect=fake_open_connection),
            mock.patch.object(server.httpx, "AsyncClient", _FakeAsyncClient),
            mock.patch.object(
                server,
                "_HEALTH_ENDPOINTS",
                [{"name": "demo-endpoint", "url": "https://example.test/health"}],
            ),
            mock.patch.object(server, "_HEALTH_ENDPOINTS_INTERMITTENT", []),
        ):
            tools = asyncio.run(server.mcp.list_tools())
            read_only_tools = [tool for tool in tools if tool.annotations and tool.annotations.readOnlyHint]

            for tool in read_only_tools:
                with self.subTest(tool=tool.name):
                    result = asyncio.run(
                        server.mcp.call_tool(
                            tool.name,
                            self._required_arguments_for_tool(tool.name),
                        )
                    )
                    content, envelope = result
                    if "result" in envelope and isinstance(envelope["result"], dict):
                        envelope = envelope["result"]
                    self.assertIsInstance(content, list)
                    self.assertIsInstance(envelope, dict)
                    self.assertEqual(tool.name, envelope["tool"])
                    self.assertIn("request_id", envelope)
                    self.assertIn("duration_ms", envelope)
                    self.assertTrue(envelope["ok"] or envelope["error"] is not None)
