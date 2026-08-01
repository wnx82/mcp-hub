"""Single source of truth for the MCP Hub version.

Everything else derives from this string:

  - `pyproject.toml` reads it statically via `[tool.setuptools.dynamic]`
  - `server.py` reports it in the MCP `initialize` handshake and `mcp_health`
  - `.github/workflows/release.yml` refuses to publish a tag that disagrees
    with it, and refuses to publish a version with no CHANGELOG entry

Bump it in the same commit as the matching CHANGELOG.md section. See the
"Releasing" section of CONTRIBUTING.md.

Versioning follows SemVer (https://semver.org). Pre-1.0, the minor number is
where breaking changes land.
"""

__version__ = "0.4.0"
