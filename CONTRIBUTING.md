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
python -m unittest
python3 scripts/check_tool_annotations.py
python3 scripts/check_security_readiness.py
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

For the fuller contributor checklist, installer smoke test, and manual local
run flow, see [docs/testing-local.md](docs/testing-local.md).

If you keep private assistant instructions next to the repo, use the tracked
[`PROJECT_INSTRUCTIONS.example.md`](PROJECT_INSTRUCTIONS.example.md) template
and keep your real `PROJECT_INSTRUCTIONS.md` untracked.

## Adding a tool

1. Put protocol logic and pure helpers in the matching `tools/<domain>.py`
   module, register ownership through `tools/registry.py`, and keep only the
   MCP server adapter in `server.py`. The tool docstring is the model-facing
   documentation, so say what it does and what it will refuse to do.
   Prefer predictable public names such as `list_*` for bounded collection
   reads, `get_*` for one snapshot or object fetch, and explicit verbs for
   mutations. When an older public name already exists, prefer adding a clear
   alias rather than silently breaking clients.
2. **If it mutates anything, add its name to `config.MUTATING_TOOLS`.** This is
   what `MCP_READ_ONLY` keys off. A mutating tool missing from that set is a
   security bug, not a style issue.
3. Never hard-code an address, path, or credential. It goes in `config.py`,
   read from the environment or a YAML file, with a safe default.
4. Reuse shared helpers such as `tools.common` and `tools.inventory` when they
   already cover config lookup, host resolution, or common error payloads.
5. Add it to the tool table in the README.

## Adding an integration

Integrations are opt-in. Add an `*_ENABLED` flag in `config.py`, default it to
`False`, document it in `.env.example`, and have the tools return
`config.integration_disabled("name")` when it is off.

## Dependency policy

Dependencies have two deliberately different representations:

- `pyproject.toml` defines the supported version range for package installs.
  Lower bounds must provide every API used by MCP Hub; upper bounds prevent an
  unreviewed breaking major or minor release.
- `requirements.txt` pins the exact versions used by the systemd installer and
  the release smoke tests, giving operators a reproducible deployment.

A dependency update must keep both files compatible. Update the range only
when support changes, update the pin to the version being tested, then run the
unit, package, and tool-registration checks. Security fixes may update only the
pin when the existing range already includes the fixed release.

## Changelog

Every user-visible change needs an entry under `## [Unreleased]` in
[CHANGELOG.md](CHANGELOG.md), in the right section (Added / Changed /
Deprecated / Removed / Fixed / Security). Internal refactors that change
nothing for an operator do not.

If your change alters a **security default** — a bind address, an auth
requirement, what `MCP_READ_ONLY` covers — it goes under `Security`, whether
it tightens or loosens things.

## Pre-push safety check

Before publishing a branch, run:

```bash
python3 scripts/check_security_readiness.py
```

It fails on common mistakes like tracked local config files, checked-in private
instructions, obvious private keys, and non-empty hard-coded tokens.

To run it automatically on every `git push`:

```bash
./scripts/install_pre_push_hook.sh
```

## Releasing

Versioning is [SemVer](https://semver.org). Pre-1.0, breaking changes bump the
**minor**. `_version.py` is the single source of truth: `pyproject.toml` reads
it, the server reports it in the MCP handshake and in `mcp_health`, and CI
refuses a mismatch.

1. Move the `[Unreleased]` entries into a new `## [x.y.z] - YYYY-MM-DD`
   section, and leave a fresh empty `[Unreleased]` above it.
2. Update the two link definitions at the bottom of the changelog.
3. Bump `__version__` in `_version.py` **in the same commit**.
4. Check it locally — this is exactly what CI runs:
   ```bash
   python scripts/check_version.py vx.y.z
   ```
5. Tag and push:
   ```bash
   git tag -a vx.y.z -m "vx.y.z" && git push origin main --tags
   ```

Pushing the tag triggers `.github/workflows/release.yml`, which re-checks that
the tag, `_version.py`, and the changelog all agree, builds the sdist and
wheel, and creates the GitHub release using that version's changelog section as
the notes. A tag whose version has no changelog entry fails before anything is
published.

## Known debt

- Docstrings are a mix of French and English (the project started as a private
  French-language tool). New docstrings should be in English; translating the
  rest is welcome and easy to review in small batches.
- `server.py` is still the composition root. Continue moving coherent protocol
  logic into `tools/*` in reviewable, behaviour-preserving chunks; domain
  modules must not import `server.py`.

## Conduct

Be decent to each other. Bad-faith participation gets you blocked without a
long conversation about it.
