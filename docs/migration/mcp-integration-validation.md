# Local Integration Validation for MCP 2026-07-28

This note records the integration scenarios that can be validated from the
repository alone on August 2, 2026, plus the operator-owned checks that still
depend on a real client, proxy, or target infrastructure.

## Repository-local validation

The following scenarios are covered automatically in the tracked test suite:

| Scenario | Evidence |
| --- | --- |
| Single local instance can start on `stdio` | [tests/test_core.py](/home/wnx/personal-projects/mcp-hub/tests/test_core.py:214) |
| Legacy and modern MCP protocol headers are accepted or rejected correctly | [tests/test_core.py](/home/wnx/personal-projects/mcp-hub/tests/test_core.py:74) |
| Stateless confirmations survive a process or instance change through shared `state.db` | [tests/test_core.py](/home/wnx/personal-projects/mcp-hub/tests/test_core.py:326) |
| Read-only tools can all be invoked without crashing even when backends are unavailable | [tests/test_read_only_smoke.py](/home/wnx/personal-projects/mcp-hub/tests/test_read_only_smoke.py:99) |
| HTTP edge validation rejects mismatched `Mcp-Name` and unknown protocol versions before the MCP app runs | [tests/test_core.py](/home/wnx/personal-projects/mcp-hub/tests/test_core.py:131) |

## What this proves

- The hub does not require a transport session to process requests.
- A shared persistent state store is enough for local multi-instance hand-off.
- Read-only tool execution degrades to structured errors when external systems
  are unavailable instead of crashing the server.
- Legacy and `2026-07-28` MCP clients remain covered at the protocol boundary.

## Operator-owned checks

The repository cannot prove the following on its own:

- Claude Desktop end-to-end usage over `stdio`
- a private internal HTTP client, if one exists outside this repository
- a real Nginx or Traefik deployment in front of the hub
- live Proxmox, Docker, Home Assistant, DSM, Notion, Cloudflare, or n8n
  backends

These remain deployment-time checks. The project documentation covers the known
limitations and the required reverse-proxy behavior:

- [Claude client guide](../claude-clients.md)
- [HTTP transport notes](../http-transport-2026-07-28.md)
- [Migration guide](./mcp-2026-07-28-guide.md)
