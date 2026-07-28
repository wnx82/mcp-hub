# Security

## What this software actually is

MCP Hub gives a large language model the ability to **execute arbitrary shell
commands, as root, on every machine listed in your `hosts.yaml`** — plus API
access to Cloudflare DNS and tunnels, Synology DSM, Proxmox, Docker, n8n and
your password vault.

That is the feature. There is no sandbox, no command allow-list, and no
meaningful blast-radius containment between hosts. `remote_exec`, `fleet_exec`,
`local_exec`, `ct_exec`, `docker_exec` and `destroy_resource` do exactly what
their names suggest.

Treat an exposed MCP Hub endpoint as **equivalent to handing out an
unauthenticated root shell on your entire fleet**. Deploy it accordingly.

## Threat model

**In scope — what the project tries to defend against:**

- Casual discovery of the endpoint (unguessable path segment + bearer token).
- Accidental destructive actions (`MCP_READ_ONLY` global kill-switch).
- Secrets leaking into tool output (automatic redaction of known token shapes
  in `read_file` and command output).
- Secrets leaking into the repository (nothing sensitive is tracked; see
  `.gitignore`, `.env.example`, `hosts.example.yaml`).

**Out of scope — what it does NOT defend against:**

- A compromised or manipulated LLM. **Prompt injection is a remote code
  execution primitive here.** If your model reads untrusted content — a web
  page, an email, a log line, an issue title — and that content instructs it to
  run a command, the hub will run that command. Nothing in this project
  prevents that.
- A token explicitly configured with `level: admin`. Access profiles can reduce
  a token's tools and target hosts, but they do not sandbox an allowed command.
- Malicious operators. Access profiles and the SQLite audit trail improve
  attribution, but they do not sandbox commands an operator is allowed to run.
- Host-to-host lateral movement. The hub's SSH key reaches every host.
- Distributed denial of service or exhaustion outside this process. The
  in-process guards are not a replacement for proxy and systemd limits.

## Deploying it without regret

Ordered roughly by how much it matters:

1. **Never bind to `0.0.0.0` on an untrusted network.** The default is
   `127.0.0.1`. Put it behind a Cloudflare Tunnel with Access policies,
   Tailscale, or WireGuard.
2. **Set `MCP_AUTH_TOKEN`.** `MCP_SECRET_PATH` is obscurity, not
   authentication — it lands in proxy logs, browser history and crash reports.
3. **Start with `MCP_READ_ONLY=true`.** Turn it off only once you have watched
   the hub run for a while and you trust what the model does with it.
4. **Give the hub its own SSH key and its own unprivileged user.** Do not reuse
   your personal key. Restrict `sudo` via a NOPASSWD allow-list of the specific
   commands you need rather than blanket root.
5. **Keep your fleet inventory minimal.** A host absent from `hosts.yaml` is a
   host the model cannot touch. This is the single most effective control
   available to you.
6. **Enable only the integrations you use.** Every `*_ENABLED=false` is an
   entire API surface removed.
7. **Scope your API tokens.** A Cloudflare token limited to one zone beats an
   account-wide token.
8. **Read `journal_query` / `job_logs` occasionally.** Know what it did.

## Access profiles

Set `MCP_AUTH_PROFILES_FILE` to a root-owned JSON file based on
`auth-profiles.example.json` to define multiple bearer tokens. Profiles support
`read`, `operate`, and `admin` levels plus glob-style tool allowlists and
host/tag restrictions. `read` cannot call mutating tools; `operate` cannot call
destructive tools; `admin` can call every explicitly allowed tool.

`MCP_READ_ONLY=true` remains the global kill switch and overrides every
profile. The legacy `MCP_AUTH_TOKEN`, when configured, is treated as an
unrestricted admin profile for backward compatibility.

## Resource guards

Every tool call passes through central in-process guards before execution:

- `MCP_RATE_LIMIT_PER_MINUTE` bounds calls per authenticated token identity.
- `MCP_MAX_ARGUMENT_BYTES` rejects oversized serialized arguments.
- `MCP_MAX_CONCURRENT_PER_HOST` bounds simultaneous calls to each target.
- `MCP_CIRCUIT_FAILURES` and `MCP_CIRCUIT_RESET_SECONDS` stop repeated calls
  to a failing target before automatically allowing a retry.
- `MCP_MUTATION_COOLDOWN_SECONDS` spaces mutating calls to the same target.

These controls reduce accidental loops and contain individual clients. They
reset when the process restarts and do not replace reverse-proxy rate limits,
systemd resource controls, or host-level isolation.

## Reporting a vulnerability

Please report security issues privately — do **not** open a public issue.

Use GitHub's [private vulnerability
reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, or email the address on the maintainer's GitHub profile.

Please include a description of the issue, reproduction steps, and the impact
you believe it has. Expect an initial response within about a week; this is a
hobby project maintained in spare time, with no SLA.

Given the threat model above, note that "an authenticated caller can run
arbitrary commands" is the intended behaviour, not a vulnerability. Reports
that *are* in scope include: authentication bypass, secret leakage into logs or
tool output, path traversal in file tools, injection through `hosts.yaml`
parsing, or read-only mode being bypassable.

## Supported versions

Pre-1.0: only the latest release receives fixes.
