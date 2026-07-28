# Private Project Instructions Template

Copy this file to `PROJECT_INSTRUCTIONS.md` and keep that customized version
out of Git. It is meant for private operator context that helps an assistant
behave safely and effectively around your real homelab.

## Topology Summary

- Main goals for this MCP Hub deployment:
- Primary assistant clients that connect to it:
- Normal operating hours / maintenance windows:
- Highest-risk systems in the fleet:

## Hosts And Roles

List the real intent behind your inventory names, especially when a host name
does not make its role obvious.

| Host key | What it is | Risk notes | Safe default actions |
|---|---|---|---|
| `prox` | Example: primary Proxmox node | Avoid reboots without confirmation | Inspect before changing |
| `nas` | Example: backup and media storage | Never run destructive cleanup casually | Prefer read-only checks |

## Tags, Groups, And Conventions

- What `role` values mean in your fleet:
- What each important tag means:
- Naming conventions the assistant should preserve:
- Services that must stay co-located:

## Do-Not-Touch Rules

- Hosts or services that must never be changed automatically:
- Commands that always require human confirmation:
- Sensitive paths that should only be read when necessary:
- APIs or integrations that are enabled but should be treated as high risk:

## Network And Access Notes

- VPN, tunnel, or bastion assumptions:
- Hosts that are only reachable from certain networks:
- Expected SSH usernames per host or role:
- Known stale DNS, recycled IP, or overlay-network traps:

## Typical MCP Workflows

- Safe read-only tasks you want the assistant to attempt first:
- Mutating tasks you are comfortable delegating:
- Tasks that should always stop after diagnosis:
- Preferred rollback or verification steps after a change:

## Troubleshooting Heuristics

- First places to check when a service is down:
- Backup locations and restore confidence notes:
- Health checks or dashboards that are more trustworthy than others:
- Known noisy alerts or false positives:

## Change Management Preferences

- How explicit a plan should be before a mutation:
- Whether the assistant should batch related changes or keep them isolated:
- What evidence you want returned after a fix:
- Whether to update documentation or notes after changes:

## Example Guardrails For Your Assistant

You can paste or adapt guidance like this into your assistant instructions:

> Default to read-only investigation first. Summarize evidence before any
> mutation. Treat storage, backup, and network edge hosts as high risk. Never
> restart multiple services at once unless the user explicitly asks for it.
