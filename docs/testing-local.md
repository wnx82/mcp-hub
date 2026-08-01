# Local Testing

This checklist is for validating MCP Hub locally before opening a PR, cutting a
release, or changing deployment-sensitive behavior.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
cp hosts.example.yaml hosts.yaml
```

Keep `MCP_READ_ONLY=true` in `.env` for all routine local validation unless you
are explicitly testing confirmation or mutation flows.

## Fast feedback

Run these first when you touched Python, docs, or tool registration:

```bash
ruff check .
python -m py_compile server.py config.py
python -m unittest
python3 scripts/check_tool_docs.py
python3 scripts/check_tool_docstrings.py
python3 scripts/check_tool_annotations.py
python3 scripts/generate_tool_reference.py --check
python3 scripts/check_repo_hygiene.py
python3 scripts/check_security_readiness.py
```

## Tool registration smoke test

This verifies that the server imports and that the MCP server sees the registered
tools without needing a real homelab:

```bash
MCP_HUB_HOME=$PWD MCP_READ_ONLY=true python -c "
import asyncio, server
tools = asyncio.run(server.mcp.list_tools())
print(len(tools), 'tools registered')
print('first five:', ', '.join(tool.name for tool in tools[:5]))
"
```

## Manual read-only server run

Use the tracked examples or your own untracked local config:

```bash
cp topology.example.yaml topology.yaml
cp endpoints.example.yaml endpoints.yaml
python server.py
```

Then confirm the expected read-only endpoint behavior from another shell:

```bash
curl -i "http://127.0.0.1:8000${MCP_SECRET_PATH:-/mcp}"
```

Expected result:

- `401` when the path is correct but no bearer token is provided.
- `404` when the path is wrong.

For the HTTP transport details introduced by MCP `2026-07-28`, including the
headers that proxies must preserve, see
**[docs/http-transport-2026-07-28.md](docs/http-transport-2026-07-28.md)**.

## Installer smoke test

The systemd installer has a dedicated local smoke test that stages files in a
temporary tree, runs the installer twice, and checks idempotence:

```bash
./scripts/test_install.sh
```

It uses `sudo` and does not touch your real `/opt/mcp-hub` or systemd unit
paths because the script overrides those destinations internally.

## Release-oriented checks

Before publishing or tagging, run the same checks plus the security readiness
scan and verify the changelog/version pair:

```bash
python3 scripts/check_version.py v$(python -c "import _version; print(_version.__version__)")
```

## When To Add More Tests

Add or extend tests when a change does any of the following:

- Alters tool registration, tool names, or tool annotations.
- Changes config parsing, environment variables, or example files.
- Modifies install, packaging, or release flows.
- Tightens or loosens security defaults.
- Refactors domain logic out of `server.py`.
