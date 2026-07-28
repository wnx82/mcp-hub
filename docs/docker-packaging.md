# Docker Packaging Notes

MCP Hub does not yet publish an official container image. This is deliberate,
not an omission in CI.

## Why it is not official yet

An official image would need to preserve the project's current security and
operational expectations rather than hiding them behind a convenient `docker
run` example.

Minimum bar for an official image:

- Run as a dedicated non-root user by default.
- Make config, SSH identity, logs, and `state.db` explicit volumes or bind
  mounts rather than silently ephemeral container state.
- Document environment variables and required files in one place.
- Ship a meaningful `HEALTHCHECK` that detects import/config failures without
  mutating anything.
- Keep the default bind address and auth expectations aligned with
  `SECURITY.md`.
- Be clear about host networking, bastion access, and SSH-agent or key-mount
  requirements.

## Constraints specific to MCP Hub

- The server is useful only if it can reach your hosts and services.
- The SSH private key is sensitive and usually needs strict file permissions.
- `state.db` contains audit and rollback data and must persist across restarts.
- `hosts.yaml`, `topology.yaml`, and `endpoints.yaml` are local operator data,
  not baked image assets.
- Some deployments need extra network reachability that container defaults may
  obscure or break.

## What an eventual image should probably expose

- `MCP_HUB_HOME` as the main mounted working directory.
- Read-only mounts for tracked example docs if desired, writable mounts for
  real local config and state.
- A non-root UID/GID that can read the mounted SSH key and write `state.db`
  and logs.
- Explicit env vars for bind address, port, auth, and integration toggles.

## Suggested implementation shape

If this becomes official later, the likely shape is:

1. A small Python base image with the wheel installed.
2. A dedicated `mcphub` user created at build time.
3. Runtime validation that required files exist and are not world-readable.
4. A read-only-by-default compose example.
5. A documented healthcheck hitting the configured MCP endpoint path.

Until those guarantees exist, systemd remains the recommended production
deployment mode.
