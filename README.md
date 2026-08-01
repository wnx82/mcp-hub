<p align="center">
  <img src="mcp-hub.png" alt="MCP Hub" width="640">
</p>

# MCP Hub

**One MCP server that gives your AI assistant the keys to your whole homelab.**

[![Release](https://img.shields.io/github/v/release/wnx82/mcp-hub?sort=semver)](https://github.com/wnx82/mcp-hub/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-2.0-purple.svg)](https://modelcontextprotocol.io/)
[![CI](https://github.com/wnx82/mcp-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/wnx82/mcp-hub/actions/workflows/ci.yml)

MCP Hub is a single [Model Context Protocol](https://modelcontextprotocol.io/)
server that sits on one machine in your network and fans out from there: SSH to
every host in your fleet, Proxmox containers, Docker, Synology DSM, Cloudflare
tunnels and DNS, n8n workflows, Notion, your password vault. Instead of running
a dozen MCP servers and wiring each one into your client, you run one and point
your assistant at it.

> *"Why is Jellyfin unreachable?"* — and the assistant checks the container, reads
> the journal, notices the tunnel ingress is stale, fixes it, and tells you what
> it did.

> ⚠️ **Read [SECURITY.md](SECURITY.md) before you deploy this.** MCP Hub hands an
> LLM root shell access across your fleet. That is the point of it, and it is
> genuinely dangerous. Defaults are safe (`127.0.0.1`, read-only); the danger
> starts when you change them.

## Troubleshooting demo

The repository includes a sanitized Asciinema recording of a complete,
observation-first troubleshooting session: failed endpoint, systemd diagnosis,
exact mutation plan, explicit confirmation, restart, and final health checks.
It uses the example inventory and contains no private infrastructure data.

```bash
asciinema play docs/troubleshooting.cast
```

See [the recording](docs/troubleshooting.cast) directly when Asciinema is not
installed; the cast format is newline-delimited JSON and remains reviewable.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Deployment](#deployment)
- [Local testing](#local-testing)
- [Rescue diagnostics](#rescue-diagnostics)
- [Configuration](#configuration)
- [Tool reference](#tool-reference)
- [Security](#security)
- [Versioning](#versioning)
- [Contributing](#contributing)

## Features

- **103 tools**, one endpoint, one config file.
- **Config-driven.** Your network lives in `hosts.yaml` and `.env`. Nothing about
  your infrastructure is baked into the code.
- **Multiplexed SSH.** Persistent control sockets, so fleet-wide commands take
  milliseconds rather than a TCP handshake each.
- **Optional integrations.** Every integration is off by default and enabled
  with a single flag. Run it as a pure SSH fleet tool if that is all you want.
- **Pluggable secrets.** Read credentials from the environment, or from a
  Bitwarden/Vaultwarden vault via `bw serve`.
- **Bearer-token authentication** on top of an unguessable endpoint path.
- **Global read-only mode**, on by default: one flag disables all 39 mutating
  tools, enforced centrally rather than tool by tool.
- **Automatic secret redaction** in file reads and command output.
- **Background jobs** with polling, logs, and a persistent SQLite state store.

## Quick start

Requires Python 3.11+ and a Linux host with SSH access to the machines you want
to manage.

```bash
git clone https://github.com/wnx82/mcp-hub.git
cd mcp-hub

python3 -m venv .venv && . .venv/bin/activate
pip install -e .

cp .env.example .env                  # then edit — see below
cp hosts.example.yaml hosts.yaml      # then edit: your fleet
chmod 600 .env hosts.yaml

python server.py
```

At minimum, set these two in `.env`:

```bash
MCP_SECRET_PATH=/$(openssl rand -hex 16)   # unguessable endpoint path
MCP_AUTH_TOKEN=$(openssl rand -hex 32)     # bearer token — the real auth
```

The server then listens on `http://127.0.0.1:8000<MCP_SECRET_PATH>`, with
`MCP_READ_ONLY=true`. Point your MCP client at that URL and send
`Authorization: Bearer <MCP_AUTH_TOKEN>`. Requests without the token get a 401;
requests to any other path get a 404.

For a local MCP client that wants `stdio` instead of HTTP, start the same hub
with:

```bash
mcp-hub --transport stdio
```

or set `MCP_TRANSPORT=stdio` in the environment before launch.

For a systemd deployment, `sudo ./deploy/install.sh` creates a dedicated
`mcphub` user and SSH key, generates both secrets into `/etc/default/mcp-hub`,
and installs the unit. It is idempotent and never overwrites existing config.
See [deploy/](deploy/).

For a complete Claude Code setup, safe token handling, connection checks, a
first read-only prompt, and the current Claude Desktop limitation, see
**[Connect MCP Hub to Claude](docs/claude-clients.md)**.

If you want your assistant to understand your private topology, host roles,
change windows, and MCP operating rules without committing any of that data,
start from [`PROJECT_INSTRUCTIONS.example.md`](PROJECT_INSTRUCTIONS.example.md)
and keep your customized `PROJECT_INSTRUCTIONS.md` local-only.

## Deployment

MCP Hub supports three execution modes:

| Mode | Intended use | Command | Support level |
|---|---|---|---|
| Editable package | Development and contributions | `pip install -e ".[dev]"` then `mcp-hub` | Supported for development |
| Direct source execution | Quick local evaluation | `python server.py` | Supported, operator manages the process |
| systemd installation | Persistent homelab deployment | `sudo ./deploy/install.sh` | Recommended for production |

The Python package and direct execution use the current checkout and its
virtualenv. They do not create a service account, SSH key, environment file, or
restart policy. The systemd installer provisions those operational pieces,
keeps local configuration intact when rerun, and installs Rescue outside the
hub virtualenv.

Container images are not an official deployment target yet. The hub needs
network access, an SSH identity, persistent `state.db`, and access to its local
inventory; operators packaging it in a container must preserve those
properties themselves.

See [`docs/docker-packaging.md`](docs/docker-packaging.md) for the current
requirements and what an official image would need to guarantee before it could
be recommended.

## Local testing

For a contributor-focused checklist covering lint, unit tests, tool
registration, generated docs, installer smoke tests, and a manual read-only
run, see **[docs/testing-local.md](docs/testing-local.md)**.

For the MCP `2026-07-28` migration summary, compatibility matrix, and rollback
procedure, see
**[docs/migration/mcp-2026-07-28-guide.md](docs/migration/mcp-2026-07-28-guide.md)**.

Before opening a PR or publishing a branch, you can also run the local release
readiness checks:

```bash
python3 scripts/check_repo_hygiene.py
python3 scripts/check_tool_annotations.py
python3 scripts/check_security_readiness.py
```

To wire the security readiness check into Git automatically on push:

```bash
./scripts/install_pre_push_hook.sh
```

## Architecture

`server.py` remains the MCP server composition root while domain code is moving
incrementally into `tools/`. SSH command construction, Cloudflare paths and
response extraction, DSM protocol metadata, inventory, and playbook builders
are already isolated. `tools/registry.py` assigns extracted tools to a domain;
that domain is included in each audit summary. New protocol logic should live
in its domain module and must not import `server.py`.

Future integrations are prioritized in
[`docs/integration-evaluation.md`](docs/integration-evaluation.md), including
their least-privilege scope and promotion gates.

## Rescue diagnostics

`mcp-hub-rescue` is a read-only local CLI designed to keep working when the
main server cannot import or its virtualenv is broken. The systemd installer
copies it to `/opt/mcp-hub-rescue` and runs it with the system Python, outside
the MCP Hub process and virtualenv.

```bash
sudo mcp-hub-rescue doctor
sudo mcp-hub-rescue status
sudo mcp-hub-rescue health
sudo mcp-hub-rescue logs --lines 50
sudo mcp-hub-rescue validate-config
```

Results are structured JSON. Rescue never imports `server.py`, `tools/*`, MCP,
or an optional integration, and this boundary is enforced by CI. The current
commands only observe and diagnose; restart, repair, and rollback operations
will be added separately with confirmation and last-known-good safeguards.

## Configuration

All git-ignored — each has a tracked `.example` template:

| File | Purpose | Required |
|---|---|---|
| [`.env`](.env.example) | Ports, auth, feature flags, API tokens | yes |
| [`hosts.yaml`](hosts.example.yaml) | Fleet inventory: hostnames, users, roles, tags | yes |
| [`topology.yaml`](topology.example.yaml) | Curated overlay: guest mapping, recycled-IP traps, do-not-touch list | no |
| [`endpoints.yaml`](endpoints.example.yaml) | HTTP health probes for `endpoints_health` | no |

A host entry is minimal by design:

```yaml
hosts:
  nas:
    hostname: nas.example.lan
    user: admin
    role: storage
    tags: [nas, backup]
    mac: "aa:bb:cc:dd:ee:01"   # optional, enables wake_host()
```

Tags are how you address groups: `fleet_exec(tag="backup", command="df -h")`.
For a copy-ready two-host inventory, start with
[`docs/examples/hosts.minimal.yaml`](docs/examples/hosts.minimal.yaml). The
larger [`hosts.example.yaml`](hosts.example.yaml) demonstrates every supported
host option.

Pair it with
[`docs/examples/topology.guarded.yaml`](docs/examples/topology.guarded.yaml)
to map Proxmox guests, record stale-address traps, and surface infrastructure
that must not be changed casually. The `_do_not_touch` entries are operational
context for the assistant, not an enforced access-control boundary; use token
profiles and host restrictions for technical enforcement.

Add [`docs/examples/endpoints.minimal.yaml`](docs/examples/endpoints.minimal.yaml)
to monitor always-on and intermittent HTTP services. Call
`endpoints_health()` for the regular set, or
`endpoints_health(include_intermittent=true)` to include services that may
normally be powered off. Responses from `200` through `399` count as healthy;
redirects are not followed.

The complete defaults, limits, integration settings, and secret-handling notes
are in the **[environment variable reference](docs/environment.md)**.

Pair those tracked examples with a private, untracked
`PROJECT_INSTRUCTIONS.md` so your assistant sees topology caveats, maintenance
windows, naming conventions, and "do not touch" guidance that should not live
in the repository.

## Tool reference

Every tool returns the same top-level envelope:

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "duration_ms": 12,
  "host": "example",
  "request_id": "4d52b1f69b974b7784bf65dd",
  "tool": "system_info"
}
```

`data` contains the tool-specific payload. Security refusals and controlled
exceptions use the same shape with `ok: false`, making chained calls and audit
correlation predictable.

The central tool wrapper also bounds request size, calls per token, concurrent
calls per target, repeated target failures, and mutation frequency. Defaults
are documented in `.env.example`; limit refusals use the same response envelope
and audit trail as every other call.

| Group | Tools |
|---|---|
| **Fleet & shell** | `list_hosts` `topology` `get_topology` `system_info` `get_system_info` `remote_exec` `local_exec` `fleet_exec` `batch_exec` `read_file` `service_ctl` `journal_query` `get_journal_entries` `apt_status` `list_package_updates` `ssh_reset_control` `wake_host` `dhcp_reservations` `endpoints_health` `infra_snapshot` `destroy_resource` |
| **Proxmox & containers** | `proxmox_list` `list_proxmox_guests` `proxmox_ct_status` `proxmox_ct_exec` `ct_exec` `ct_write_file` `pbs_status` `docker_ps` `list_docker_containers` `docker_exec` |
| **Synology DSM** | `dsm_health` `dsm_system_info` `dsm_storage` `dsm_shares` `dsm_packages` `dsm_package_control` `dsm_updates` `dsm_connections` `dsm_logs` `dsm_power` `dsm_file_list` `dsm_file_search` `dsm_download_list` `dsm_download_create` `dsm_download_control` `dsm_api` `dsm_relogin` |
| **Cloudflare** | `cloudflare_tunnels_list` `list_cloudflare_tunnels` `cloudflare_tunnel_get` `cloudflare_tunnel_config_get` `cloudflare_tunnel_config_update` `cloudflare_dns_list` `cloudflare_dns_create` `cloudflare_dns_delete` `cf_ingress_dump` `get_cloudflare_tunnel_ingress` `cloudflare_api` |
| **n8n** | `n8n_health` `n8n_list_workflows` `n8n_get_workflow` `n8n_activate_workflow` `n8n_deactivate_workflow` `n8n_list_executions` `n8n_get_execution` `n8n_call_webhook` |
| **Notion** | `notion_search` `notion_get_page` `notion_create_page` `notion_update_page` `notion_archive_page` `notion_query_database` `notion_get_block_children` `notion_append_blocks` `notion_append_table_row` `notion_delete_block` `notion_reload_token` |
| **Vault** | `vault_search` `vault_get_item` `vault_get_field` `vault_create_item` `vault_update_item` `vault_list_folders` |
| **LM Studio** | `lmstudio_status` `lmstudio_load` `lmstudio_unload` |
| **Guided diagnostics** | `diagnose_service` `diagnose_endpoint` `audit_host` `check_backup_chain` |
| **Jobs & introspection** | `job_run` `job_status` `job_list` `job_logs` `mcp_health` `get_mcp_health` `mcp_stats` `get_mcp_stats` `audit_export` `plan_mutation` `confirm_mutation` `rollback_change` |

The **[complete generated tool reference](docs/tool-reference.md)** expands
every group into a table with each tool's exact signature and model-facing
description. CI verifies it against the registered functions.

Guided diagnostics always stop after observation. They return evidence,
assessment, and suggested next steps with `correction_applied: false`;
`check_backup_chain` is a freshness and storage signal, not proof that a restore
will succeed.

## Security

MCP Hub is a remote code execution service by design. Before exposing it:

- Keep the default `127.0.0.1` bind, or put it behind a tunnel with access
  policies.
- Set `MCP_AUTH_TOKEN` — the secret URL path is obscurity, not authentication.
- Leave `MCP_READ_ONLY=true` until you trust what your model does with it.
- Keep the resource-guard defaults enabled, then tune them from observed audit
  traffic rather than disabling them.
- Give it a dedicated SSH key and a minimal `hosts.yaml`.

Full threat model, hardening guide, and vulnerability reporting:
**[SECURITY.md](SECURITY.md)**.

For a local pre-publish checklist and optional Git hook that catches common
secret-leak mistakes before push, see
[`scripts/check_security_readiness.py`](scripts/check_security_readiness.py)
and [`scripts/install_pre_push_hook.sh`](scripts/install_pre_push_hook.sh).

## Versioning

[SemVer](https://semver.org). Pre-1.0, breaking changes bump the **minor** — so
read the `Changed` and `Removed` notes before upgrading one.
`_version.py` is the single source of truth; the running server reports it via
`mcp-hub --version`, in the MCP handshake, and in `mcp_health`.

Every release is documented in **[CHANGELOG.md](CHANGELOG.md)**, with
security-relevant changes called out in their own section.

## Contributing

Issues and pull requests are welcome — bug reports, new integrations, and
documentation fixes especially. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © wnx82
