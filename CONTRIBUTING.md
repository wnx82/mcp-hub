# Contributing

Thanks for taking a look. This is a hobby project maintained in spare time —
issues and pull requests are welcome, and slow replies are likely.

## Before you open a PR

**Never commit anything about a real network.** No IP addresses, hostnames,
MAC addresses, domains, vault item UUIDs, API tokens, or personal notes. The
inventory files (`hosts.yaml`, `topology.yaml`, `endpoints.yaml`) and `.env`
are git-ignored for that reason — edit the `.example` templates instead, and
use documentation ranges (`192.0.2.0/24`, `example.com`) in them.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
cp hosts.example.yaml hosts.yaml
```

## Checks

```bash
ruff check .
python -m py_compile server.py config.py
```

`ruff format` is intentionally *not* enforced — reformatting the monolith
wholesale would bury every subsequent diff. Match the style of the code around
you instead.

A quick functional smoke test, no fleet required:

```bash
MCP_HUB_HOME=$PWD MCP_READ_ONLY=true python -c "
import asyncio, server
print(len(asyncio.run(server.mcp.list_tools())), 'tools registered')
"
```

## Adding a tool

1. Write it in `server.py` with an `@mcp.tool()` decorator and a docstring —
   the docstring *is* the model-facing documentation, so say what the tool
   does, what its arguments mean, and what it will refuse to do.
2. **If it mutates anything, add its name to `config.MUTATING_TOOLS`.** This is
   what `MCP_READ_ONLY` keys off. A mutating tool missing from that set is a
   security bug, not a style issue.
3. Never hard-code an address, path, or credential. It goes in `config.py`,
   read from the environment or a YAML file, with a safe default.
4. Add it to the tool table in the README.

## Adding an integration

Integrations are opt-in. Add an `*_ENABLED` flag in `config.py`, default it to
`False`, document it in `.env.example`, and have the tools return
`config.integration_disabled("name")` when it is off.

## Known debt

- Docstrings are a mix of French and English (the project started as a private
  French-language tool). New docstrings should be in English; translating the
  rest is welcome and easy to review in small batches.
- `server.py` is a ~3500-line monolith. Splitting it into `tools/*` modules is
  planned but not urgent — behaviour-preserving, reviewable chunks preferred
  over one large move.

## Conduct

Be decent to each other. Bad-faith participation gets you blocked without a
long conversation about it.
