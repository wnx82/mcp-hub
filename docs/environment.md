# Environment Variable Reference

Precedence is process environment, then `.env` next to `config.py`, then the
default below. For systemd installations, `/etc/default/mcp-hub` is loaded by
the unit before Python starts and therefore takes precedence over `.env`.

Boolean values accept `1`, `true`, `yes`, or `on` (case-insensitive). Empty or
invalid integer values fall back to the documented default.

## Paths and transport

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `MCP_HUB_HOME` | repository directory | no | Base directory for local configuration and state. |
| `MCP_HOSTS_FILE` | `$MCP_HUB_HOME/hosts.yaml` | no | Fleet inventory path. |
| `MCP_TOPOLOGY_FILE` | `$MCP_HUB_HOME/topology.yaml` | no | Curated topology overlay path. |
| `MCP_ENDPOINTS_FILE` | `$MCP_HUB_HOME/endpoints.yaml` | no | HTTP probe inventory path. |
| `MCP_STATE_DB` | `$MCP_HUB_HOME/state.db` | sensitive | SQLite audit, jobs, cache, and rollback snapshots. |
| `MCP_LOG_FILE` | `$MCP_HUB_HOME/mcp-hub.log` | sensitive | Rotating application log path. |
| `MCP_BIND_ADDR` | `127.0.0.1` | no | HTTP listen address. Keep loopback unless protected by a proxy or tunnel. |
| `MCP_PORT` | `8000` | no | HTTP listen port. |
| `MCP_SECRET_PATH` | `/mcp` | sensitive | Streamable HTTP endpoint path; obscurity, not authentication. |
| `MCP_PUBLIC_HOST` | empty | no | Public hostname allowed by DNS-rebinding protection. |

## Authentication and safety

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `MCP_AUTH_TOKEN` | empty | yes | Legacy unrestricted admin bearer token. |
| `MCP_AUTH_PROFILES_FILE` | empty | sensitive | Root-owned JSON file containing scoped bearer-token profiles. |
| `MCP_READ_ONLY` | `true` | no | Global kill switch for every registered mutating tool. |
| `MCP_CONFIRMATION_MODE` | `sensitive` | no | Confirmation policy: `off`, `sensitive`, or `all`. |
| `MCP_CONFIRMATION_TTL_SECONDS` | `300` | no | Lifetime of a one-time mutation plan, clamped to 30-900 seconds. |
| `MCP_RATE_LIMIT_PER_MINUTE` | `120` | no | Maximum accepted calls per authenticated identity per process. |
| `MCP_MAX_ARGUMENT_BYTES` | `200000` | no | Maximum serialized arguments for one tool call. |
| `MCP_MAX_CONCURRENT_PER_HOST` | `4` | no | Simultaneous accepted calls per resolved target. |
| `MCP_CIRCUIT_FAILURES` | `3` | no | Consecutive target failures before opening its circuit. |
| `MCP_CIRCUIT_RESET_SECONDS` | `60` | no | Time before an open target circuit allows another attempt. |
| `MCP_MUTATION_COOLDOWN_SECONDS` | `1` | no | Minimum spacing between mutations on the same target. |
| `MCP_SNAPSHOT_MAX_BYTES` | `65536` | no | Largest file state captured for rollback. |
| `MCP_SNAPSHOT_RETENTION_DAYS` | `7` | no | Retention for rollback snapshots in `state.db`. |

## SSH and execution

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `MCP_SSH_KEY` | `~/.ssh/id_ed25519` | sensitive | Private key used for the fleet. |
| `MCP_SSH_CONTROL_DIR` | `~/.ssh/mcp-hub-control` | no | OpenSSH multiplexing socket directory. |
| `MCP_DEFAULT_HYPERVISOR` | `prox` | no | Default `hosts.yaml` key for Proxmox-oriented tools. |
| `MCP_DEFAULT_TIMEOUT` | `60` | no | Default command timeout in seconds. |
| `MCP_MAX_TIMEOUT` | `300` | no | Maximum caller-selectable command timeout. |
| `MCP_MAX_STDOUT_BYTES` | `200000` | no | Maximum returned stdout bytes per command. |
| `MCP_MAX_STDERR_BYTES` | `50000` | no | Maximum returned stderr bytes per command. |

## Secrets provider

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `MCP_SECRETS_PROVIDER` | `env` | no | Credential source: `env` or `vaultwarden`. |
| `BW_SERVE_URL` | `http://127.0.0.1:8090` | no | Local Bitwarden CLI `bw serve` endpoint. |

## Cloudflare

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `CLOUDFLARE_ENABLED` | `false` | no | Advertise Cloudflare as enabled in hub health. |
| `CLOUDFLARE_API_TOKEN` | empty | yes | Scoped Cloudflare API bearer token. |
| `CLOUDFLARE_ACCOUNT_ID` | empty | sensitive | Account containing managed tunnels. |
| `CLOUDFLARE_ZONE_ID` | empty | sensitive | Default DNS zone. |
| `CLOUDFLARE_API_BASE` | `https://api.cloudflare.com/client/v4` | no | API base URL, primarily for testing or proxies. |
| `CLOUDFLARE_DEFAULT_TUNNEL_ID` | empty | sensitive | Default tunnel for ingress-oriented tools. |

## n8n

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `N8N_ENABLED` | `false` | no | Advertise n8n as enabled in hub health. |
| `N8N_API_URL` | `http://localhost:5678/api/v1` | no | n8n REST API base URL. |
| `N8N_API_KEY` | empty | yes | n8n API key. |
| `N8N_READ_ONLY` | `false` | no | Integration-specific mutation kill switch; global read-only still wins. |

## Synology DSM

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `DSM_ENABLED` | `false` | no | Advertise DSM as enabled in hub health. |
| `DSM_WEBAPI_BASE` | `http://localhost:5000/webapi` | no | DSM WebAPI base URL. |
| `DSM_SESSION_NAME` | `FileStation` | no | DSM API session name. |
| `DSM_USER` | empty | yes | DSM username when using the environment provider. |
| `DSM_PASSWORD` | empty | yes | DSM password when using the environment provider. |
| `DSM_CRED_ITEM_UUID` | empty | sensitive | Vault item UUID containing DSM credentials. |
| `DSM_SSH_HOST` | `dsm` | no | Inventory key used by DSM log helpers requiring SSH. |

## Notion

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `NOTION_ENABLED` | `false` | no | Advertise Notion as enabled in hub health. |
| `NOTION_TOKEN` | empty | yes | Notion integration token for the environment provider. |
| `NOTION_TOKEN_ITEM_UUID` | empty | sensitive | Vault item UUID containing the Notion token. |
| `NOTION_API_BASE` | `https://api.notion.com/v1` | no | Notion API base URL. |
| `NOTION_VERSION` | `2022-06-28` | no | Value sent in the `Notion-Version` header. |

## LM Studio and Wake-on-LAN

| Variable | Default | Secret | Purpose |
|---|---|---:|---|
| `LMSTUDIO_ENABLED` | `false` | no | Advertise LM Studio as enabled in hub health. |
| `LMSTUDIO_HOST` | empty | no | `hosts.yaml` key for the LM Studio machine. |
| `LMSTUDIO_PORT` | `1234` | no | LM Studio API port. |
| `LMSTUDIO_PUBLIC_ENDPOINT` | empty | no | Optional endpoint reported by status tools. |
| `WOL_ENABLED` | `false` | no | Advertise Wake-on-LAN as enabled in hub health. |
| `WOL_BROADCAST` | `255.255.255.255` | no | Broadcast address for magic packets. |

## Secret handling

Never commit `.env`, `/etc/default/mcp-hub`, access-profile JSON, `state.db`, or
private keys. Use mode `0600` for secret-bearing files. Prefer scoped
integration tokens and access profiles over the legacy unrestricted
`MCP_AUTH_TOKEN`.
