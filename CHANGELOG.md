# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, the **minor** number is where breaking changes land. Read the
`Changed` and `Removed` sections before upgrading a minor.

Because this project can execute code across your fleet, every entry that
changes a security default is called out under **Security**. Read those first.

<!--
Add new entries under [Unreleased] as you go. At release time, rename that
heading to the version, add a fresh empty [Unreleased], and bump _version.py in
the same commit. Sections, in order: Added / Changed / Deprecated / Removed /
Fixed / Security.
-->

## [Unreleased]

### Added

- A sanitized Asciinema recording demonstrates a complete observation,
  confirmation, correction, and verification troubleshooting session.
- A complete Claude Code connection guide covers private and project scopes,
  bearer-token handling, verification, troubleshooting, and Desktop limits.
- A complete environment reference documents every runtime variable, default,
  secret classification, and operational purpose.
- A generated reference lists all MCP tools by group with their exact
  signatures and model-facing descriptions.
- CI now exercises the systemd installer twice in an isolated staging tree,
  verifying installed files and preservation of local configuration.
- CI builds and installs the wheel in a clean virtualenv, then checks both
  console commands and imports from outside the source checkout.
- Targeted tests now cover secret redaction, the global read-only guard,
  typed environment parsing, and all YAML-backed inventory loaders.
- A coherent fictional inventory, topology, and endpoint fixture set validates
  configuration loading without access to a real homelab.
- CI now rejects drift between registered MCP tools, the README tool table,
  and the advertised tool count.
- CI now rejects tracked Python caches, build directories, wheels, source
  archives, and package metadata.
- `plan_mutation` and `confirm_mutation` provide short-lived, profile-bound,
  one-time confirmation for exact mutating calls.
- Every registered tool now publishes MCP `readOnlyHint`, `destructiveHint`,
  `idempotentHint`, and `openWorldHint` behavior annotations.
- `audit_export` exposes a bounded 30-day audit trail correlated by request,
  profile, tool, host, result status, and duration without storing full payloads.
- A central resource limiter now enforces per-token request quotas, argument
  size bounds, per-target concurrency, mutation cooldowns, and circuit breakers.
- `rollback_change` restores one profile-bound snapshot for Cloudflare tunnel
  configuration, supported Notion page fields, or bounded LXC file writes.
- Observation-only `diagnose_service`, `diagnose_endpoint`, `audit_host`, and
  `check_backup_chain` playbooks return evidence before suggesting correction.

### Changed

- Installer destinations and external setup steps can be overridden for safe
  staging tests while production defaults remain unchanged.
- Tool modularisation has started with `list_hosts` moved to
  `tools/inventory.py` while preserving its public MCP name and schema.
- SSH construction, Cloudflare protocol helpers, DSM metadata, and playbook
  builders now live in domain modules connected by `tools/registry.py`; audit
  summaries include the resolved domain.
- Future integrations now have a risk-ranked implementation order and explicit
  least-privilege promotion gates in `docs/integration-evaluation.md`.
- All model-facing tool descriptions are now in English, with a CI guard
  preventing missing descriptions or French text from being reintroduced.
- The README now distinguishes development, direct-source, and recommended
  systemd deployment modes and documents their operational responsibilities.
- The contribution guide now defines how compatible dependency ranges and
  reproducible deployment pins must be maintained together.
- All MCP tool results now use a common `ok`, `data`, `error`, `duration_ms`,
  `host`, `request_id`, and `tool` envelope, including security refusals.

### Fixed

- Tool documentation checks now resolve the repository root when executed as
  scripts, matching their GitHub Actions invocation.

### Security

- Optional bearer-token access profiles now provide `read`, `operate`, and
  `admin` levels with tool, host, and tag restrictions. The legacy
  `MCP_AUTH_TOKEN` remains an unrestricted admin token for compatibility, and
  the global read-only switch still overrides every profile.
- Runaway calls are contained by configurable in-process resource guards.
- Reversible mutations now capture prior state in a mode-`0600`, retained
  SQLite store and refuse changes that cannot be snapshotted safely.

## [0.2.0] - 2026-07-28

### Added

- A dependency-free, read-only `mcp-hub-rescue` CLI with `status`, `health`,
  `logs`, `validate-config`, `diagnose`, and `doctor` commands. It is installed
  outside the hub virtualenv and never imports the main server or integrations.
- Rescue isolation tests run without installing MCP Hub dependencies and verify
  that diagnostics still import when `server.py` is broken.

## [0.1.0] - 2026-07-28

First public release. Extracted from a private deployment and made
config-driven so it can run against any fleet.

### Added

- **85 MCP tools** over a single streamable-http endpoint, covering fleet SSH
  execution, Proxmox, Docker, Synology DSM, Cloudflare tunnels and DNS, n8n,
  Notion, a Bitwarden/Vaultwarden bridge, LM Studio, Wake-on-LAN, background
  jobs, and health probes.
- **`config.py`**, a single place resolving every site-specific value from the
  environment, `.env`, and YAML inventory. No address, hostname, domain, or
  credential identifier lives in the tool code.
- **`hosts.yaml`** fleet inventory with roles and tags; `fleet_exec` targets a
  tag rather than a hand-maintained list.
- **`topology.yaml`** (optional): curated overlay for guest mapping,
  recycled-IP traps, and a hard do-not-touch list — the knowledge a live scan
  cannot recover.
- **`endpoints.yaml`** (optional): HTTP health probes for `endpoints_health`,
  with a separate `intermittent` list for hosts that are often powered down.
- **Opt-in integrations.** Each is off until its `*_ENABLED` flag is set, and
  disabled ones return a clear error rather than failing obscurely.
- **Pluggable secrets provider**: `env` (default) or `vaultwarden` via a
  `bw serve` daemon. DSM and Notion credentials resolve through either.
- **Bearer-token authentication** (`MCP_AUTH_TOKEN`), enforced by ASGI
  middleware with a constant-time comparison.
- **Multiplexed SSH** with a configurable ControlPath, so fleet-wide commands
  reuse one connection per host.
- **Packaging**: `pyproject.toml` with a `mcp-hub` entry point, pinned
  `requirements.txt`, systemd unit templates, and an idempotent
  `deploy/install.sh` that provisions a dedicated user, a dedicated SSH key,
  and generated secrets without ever overwriting existing config.
- `--version` / `-V` flag, and a `version` field in `mcp_health`.
- CI: lint, an import-and-register smoke test across Python 3.11–3.13, a
  read-only enforcement test, and a guard rejecting private network data or a
  tracked real config file.

### Security

- **`MCP_READ_ONLY` defaults to `true`.** All 37 mutating tools refuse until
  it is explicitly turned off. Enforcement is centralised by wrapping tool
  registration, so a tool cannot silently escape the guard — but a *new*
  mutating tool must be added to `config.MUTATING_TOOLS`, which CI checks.
- **Binds `127.0.0.1` by default** instead of `0.0.0.0`. Exposing remote shell
  execution on a network is now a deliberate act.
- **`SECURITY.md`** states the threat model without softening it: this is
  remote code execution as a service, and prompt injection is an RCE primitive
  against it. It also lists what the project explicitly does *not* defend
  against.
- The MCP `initialize` handshake now reports the hub's own version. Previously
  it fell back to the installed `mcp` library version, misinforming clients.
- Secret redaction is applied to file reads and command output.

### Known limitations

- Docstrings are a mix of French and English; the project began as a private
  French-language tool.
- `server.py` is a ~3600-line monolith. Splitting it into `tools/*` modules is
  planned, in reviewable behaviour-preserving chunks.
- `ruff format` and the `UP` lint rules are not enforced yet — applying either
  wholesale would bury every subsequent diff.
- There is one trust level. Anyone holding the bearer token has full access;
  there is no per-tool ACL and no multi-user model.

[Unreleased]: https://github.com/wnx82/mcp-hub/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/wnx82/mcp-hub/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/wnx82/mcp-hub/releases/tag/v0.1.0
