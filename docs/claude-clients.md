# Connect MCP Hub to Claude

MCP Hub uses Streamable HTTP with a static bearer token. Claude Code supports
that combination directly and is the recommended Claude client today.

## Before connecting

The URL is the public or locally reachable base URL plus `MCP_SECRET_PATH`.
Keep the token out of shell history and project files:

```bash
export MCP_HUB_URL='https://mcp.example.com/your-secret-path'
read -rsp 'MCP Hub token: ' MCP_HUB_TOKEN
export MCP_HUB_TOKEN
printf '\n'
```

When Claude Code runs on the hub itself, the URL can instead be
`http://127.0.0.1:8000/your-secret-path`. From another machine, use an
authenticated private tunnel or HTTPS reverse proxy; do not publish port 8000
directly.

Start with `MCP_READ_ONLY=true` and preferably a `read` access-profile token.

## Claude Code: local private configuration

Run this from the project where MCP Hub should be available:

```bash
claude mcp add \
  --transport http \
  --scope local \
  --header "Authorization: Bearer ${MCP_HUB_TOKEN}" \
  mcp-hub "${MCP_HUB_URL}"
```

Local scope stores the server under this project in `~/.claude.json`; it does
not create a tracked project file. The expanded token is stored in that private
user configuration, so keep the file accessible only to your account:

```bash
chmod 600 ~/.claude.json
claude mcp get mcp-hub
claude mcp list
```

Start Claude Code, open `/mcp`, and verify that `mcp-hub` is connected and
advertises its tools:

```bash
claude
```

Use an observation-only first prompt:

```text
Use MCP Hub to list the configured hosts and audit the host named "nas".
Do not call mutating tools and do not propose a correction until you have
summarized the observations.
```

Remove the private configuration with:

```bash
claude mcp remove mcp-hub
```

## Claude Code: shareable project template

For a trusted team repository, create `.mcp.json` without a literal URL or
token:

```json
{
  "mcpServers": {
    "mcp-hub": {
      "type": "http",
      "url": "${MCP_HUB_URL}",
      "headers": {
        "Authorization": "Bearer ${MCP_HUB_TOKEN}"
      },
      "timeout": 300000
    }
  }
}
```

Each operator exports their own variables before launching Claude Code.
Project-scoped servers require explicit approval when the workspace is first
trusted. Never commit a concrete bearer token.

## Claude Desktop limitation

Claude Desktop remote servers are added through **Settings > Connectors**, not
through `claude_desktop_config.json`. At present, remote connectors support
OAuth or authless servers, while MCP Hub intentionally requires a static bearer
token. Therefore a direct Claude Desktop remote connection is not supported
until MCP Hub gains OAuth.

Do not disable `MCP_AUTH_TOKEN` to work around this. Use Claude Code, or put a
standards-compliant OAuth gateway in front of MCP Hub and retain the Hub's
network isolation.

Official references:

- [Claude Code MCP configuration](https://code.claude.com/docs/en/mcp)
- [Claude remote connectors](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers)

## Troubleshooting

| Symptom | Check |
|---|---|
| `401 Unauthorized` | The token differs from `MCP_AUTH_TOKEN` or the selected access-profile token. |
| `404 Not Found` | The URL does not include the exact `MCP_SECRET_PATH`. |
| Connection refused | The hub is not listening, or a local URL is being used from another machine. |
| Server pending approval | Start Claude Code interactively and approve the project-scoped server. |
| Tools are read-only | This is expected with `MCP_READ_ONLY=true` or a profile at level `read`. |
| Tool target refused | The access profile does not allow that host or tag. |

On the hub, correlate client failures with:

```bash
sudo systemctl status mcp-hub
sudo journalctl -u mcp-hub -n 100 --no-pager
mcp-hub-rescue doctor
```
