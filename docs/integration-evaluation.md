# Integration Evaluation

Status: accepted evaluation, implementation deferred until the core controls
are proven in production.

## Decision

Implement future integrations in this order:

1. Prometheus read-only queries and target health.
2. ntfy outbound notifications.
3. Restic/Borg repository observation.
4. Home Assistant state reads, then separately confirmed actions.
5. Kubernetes read-only diagnostics.

Grafana should follow Prometheus only where it adds alert or dashboard context.
Apprise should follow ntfy only when routing to many notification providers is
actually required.

## Evaluation Matrix

| Candidate | Initial scope | Value | Risk | Effort | Priority |
|---|---|---:|---:|---:|---:|
| Prometheus | instant/range queries, targets, alerts | high | low | low | 1 |
| Grafana | alert state and dashboard links | medium | medium | medium | 2b |
| ntfy | allowlisted outbound notifications | high | low | low | 2 |
| Apprise | notifications through named configurations | medium | medium | medium | 2b |
| Restic/Borg | snapshots, freshness, bounded checks | high | medium | medium | 3 |
| Home Assistant | entities and state, then services | medium | high | medium | 4 |
| Kubernetes | events, workloads, logs, health | medium | very high | high | 5 |

## 1. Prometheus and Grafana

Prometheus is the best first addition. Its stable JSON HTTP API lives under
`/api/v1`, so a narrow adapter can expose `prometheus_query`,
`prometheus_targets`, and `prometheus_alerts` without shell access. Queries must
have a timeout, maximum range, maximum returned series, and response-size
limit. Prometheus endpoints must remain private; its own security guidance says
not to expose component HTTP endpoints publicly.

Grafana is useful for alert state and links back to dashboards, but not required
for metrics reasoning. Its HTTP API can also manage dashboards, users, data
sources, and alerts, and its legacy `/api` surface is moving toward versioned
`/apis` endpoints. Start with a read-only service account and do not expose a
generic Grafana request tool.

Sources: [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/),
[Prometheus security model](https://prometheus.io/docs/operating/security/),
[Grafana HTTP API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/).

## 2. ntfy and Apprise

ntfy is the preferred first notification adapter because publishing is a small
HTTP POST/PUT operation. Expose one `notify` tool with allowlisted topics,
bounded title/body lengths, per-token quotas, and no caller-controlled server
URL. Treat it as an `operate` mutation and return the provider message ID.

Apprise becomes attractive only if one MCP tool must route to many providers.
Use server-side named configurations and tags; never accept an Apprise URL from
tool arguments because those URLs contain credentials and can select arbitrary
destinations. The Apprise API intentionally ships without authentication or
TLS requirements, so it must stay on a private network or behind an
authenticated proxy.

Sources: [ntfy publishing](https://docs.ntfy.sh/publish/),
[Apprise API](https://github.com/caronc/apprise-api).

## 3. Restic and Borg

Extend `check_backup_chain` with explicit adapters rather than a raw backup
shell. Initial tools should list snapshots, calculate age by host/path/tag, and
run bounded read-only consistency checks against repository aliases configured
on the hub. Repository locations, passwords, and key files must never be tool
arguments or output.

Full data verification is expensive: Restic documents that `check --read-data`
reads every pack, while Borg documents that `check --verify-data` is
time-consuming. Those checks need job execution, per-repository concurrency of
one, long cooldowns, and a hard timeout. Repair flags must not be exposed;
Borg explicitly warns that `--repair` can cause additional data loss.

Sources: [Restic repository checks](https://restic.readthedocs.io/en/stable/045_working_with_repos.html#checking-integrity-and-consistency),
[Borg check](https://borgbackup.readthedocs.io/en/stable/usage/check.html).

## 4. Home Assistant

Start with `homeassistant_entities` and `homeassistant_state` over the JSON REST
API. Use a dedicated Home Assistant user and store its long-lived bearer token
through the existing secrets provider. Only after read behavior is stable
should `homeassistant_call_service` be added, with domain/service/entity
allowlists, mutation confirmation, cooldowns, and a returned set of changed
states.

Do not expose a generic REST path because the same API includes service calls
that control physical devices. Physical effects also make generic rollback
unreliable.

Source: [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/).

## 5. Kubernetes

Kubernetes is last because its API supports read, create, update, patch, and
delete across both namespaced and cluster-scoped resources. Begin with a
dedicated ServiceAccount restricted by RBAC to `get`, `list`, and `watch` in
explicit namespaces. Initial tools should cover workload status, events, pod
logs, and rollout observation only. Do not expose pod exec, secret reads,
arbitrary proxying, apply, or delete.

If mutations are added later, require a separate token/profile and use
resource-specific tools, server-side dry-run during planning, exact object
snapshots, and confirmation. Cluster-scoped writes remain out of scope.

Source: [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/).

## Promotion Gates

An integration may move from evaluation to implementation only when:

- It has a dedicated module registered in `tools/registry.py`.
- Credentials are least-privilege and resolved by the secrets provider.
- Read and mutation tools are separate; no generic request or shell escape is
  exposed.
- Argument, response, rate, concurrency, and timeout bounds are specified.
- Mutations have planning/confirmation and either a tested rollback or an
  explicit statement that rollback is impossible.
- Unit tests run without contacting a real service, and disabled integration
  behavior is covered.
