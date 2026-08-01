# HTTP Transport Notes for MCP 2026-07-28

MCP Hub exposes MCP over Streamable HTTP, so reverse proxies and gateways must
preserve the MCP transport headers introduced by the `2026-07-28`
specification.

## Required request headers

For Streamable HTTP POST requests, clients are expected to send:

- `MCP-Protocol-Version`
- `Mcp-Method`
- `Mcp-Name` when the request names a tool, prompt, or resource

MCP Hub now logs these headers at the HTTP boundary together with the resolved
access profile, request path, and client address. This helps operators confirm
that a proxy is forwarding MCP metadata rather than stripping it.

Example log shape:

```text
http request method=POST path=/mcp client=127.0.0.1 profile=legacy-admin mcp_protocol=2026-07-28 mcp_method=tools/call mcp_name=list_hosts origin=-
```

## Reverse proxy requirements

- Preserve `Authorization`
- Preserve `MCP-Protocol-Version`
- Preserve `Mcp-Method`
- Preserve `Mcp-Name`
- Do not add sticky-session requirements
- Keep request body size and timeout limits large enough for MCP JSON-RPC calls

Because MCP Hub does not rely on transport sessions, a proxy must not inject a
cookie affinity requirement just to keep the protocol working.

## Nginx example

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_set_header Authorization $http_authorization;
    proxy_set_header MCP-Protocol-Version $http_mcp_protocol_version;
    proxy_set_header Mcp-Method $http_mcp_method;
    proxy_set_header Mcp-Name $http_mcp_name;

    proxy_read_timeout 300s;
    client_max_body_size 2m;
}
```

## Traefik example

With Traefik, the main requirement is to forward requests unchanged and avoid
session affinity for the MCP route unless some other non-MCP concern requires
it. If you use custom middleware or WAF rules, explicitly allow:

- `Authorization`
- `MCP-Protocol-Version`
- `Mcp-Method`
- `Mcp-Name`

## Operational checks

After proxy deployment:

1. Send a read-only MCP request through the public MCP URL.
2. Confirm the hub log line includes `mcp_protocol`, `mcp_method`, and
   `mcp_name`.
3. Confirm the request succeeds without any session cookie.
4. If a load balancer is present, verify it is not configured with sticky
   sessions solely for MCP Hub.
