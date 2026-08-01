# Architecture MCP actuelle

Date d'audit: 2026-08-01T11:50:41Z

## Resume executif

MCP Hub utilise aujourd'hui le SDK Python `mcp` en version `1.28.1` via
`FastMCP`, avec un point d'entree principal unique dans [server.py](/home/wnx/personal-projects/mcp-hub/server.py).
Le transport effectivement cable et documente est `HTTP Streamable`.
Le serveur est deja configure en `stateless_http=True`, ce qui reduit une
partie du delta attendu pour la migration protocolaire `2026-07-28`.

## SDK et implementation

- Paquet MCP: `mcp==1.28.1`
- Implementation utilisee: `mcp.server.fastmcp.FastMCP`
- Version projet exposee au handshake: `0.3.0` via `mcp._mcp_server.version = __version__`
- Dependances MCP critiques:
  - `mcp==1.28.1`
  - `httpx==0.28.1`
  - `PyYAML==6.0.3`
  - `uvicorn==0.51.0`

Sources principales:

- [pyproject.toml](/home/wnx/personal-projects/mcp-hub/pyproject.toml)
- [requirements.txt](/home/wnx/personal-projects/mcp-hub/requirements.txt)
- [server.py](/home/wnx/personal-projects/mcp-hub/server.py)

## Gestionnaire de dependances

Le depot est configure autour de `pip` et de `setuptools`.

Indices releves:

- fichier [pyproject.toml](/home/wnx/personal-projects/mcp-hub/pyproject.toml)
- fichier [requirements.txt](/home/wnx/personal-projects/mcp-hub/requirements.txt)
- absence de `uv.lock`
- absence de `poetry.lock`

Un virtualenv local [`.venv`](/home/wnx/personal-projects/mcp-hub/.venv) existe, mais il
est incomplet cote outillage: `pip` et `pytest` n'y sont pas disponibles comme
modules executables au moment de l'audit.

## Transports identifies

### HTTP Streamable

Transport principal confirme.

Elements observes:

- `FastMCP(..., stateless_http=True, streamable_http_path=SECRET_PATH, ...)`
- `mcp.run(transport="streamable-http")` quand aucun token n'est configure
- `mcp.streamable_http_app()` + `uvicorn.run(...)` quand l'auth bearer est activee
- documentation client dans [docs/claude-clients.md](/home/wnx/personal-projects/mcp-hub/docs/claude-clients.md)

### stdio

Non confirme dans le code actuel.

Constat:

- aucun `mcp.run(transport="stdio")` trouve
- la documentation d'exploitation est centree sur HTTP
- l'objectif de roadmap "maintien du fonctionnement actuel en stdio" ne
  correspond pas a l'implementation documentee a la date d'audit

### SSE

Non detecte.

## Points d'entree et fichiers responsables

- Serveur MCP principal: [server.py](/home/wnx/personal-projects/mcp-hub/server.py)
- Configuration transport/auth/securite: [config.py](/home/wnx/personal-projects/mcp-hub/config.py)
- Script console package:
  - `mcp-hub = "server:main"`
  - `mcp-hub-rescue = "rescue.cli:main"`
- Documentation client Claude: [docs/claude-clients.md](/home/wnx/personal-projects/mcp-hub/docs/claude-clients.md)
- Validation locale: [docs/testing-local.md](/home/wnx/personal-projects/mcp-hub/docs/testing-local.md)

## Clients connus / cibles documentees

- Claude Code: client recommande et explicitement documente
- Claude Desktop: limitation documentee, pas de support direct actuel avec
  bearer token statique sans passerelle OAuth
- Client HTTP interne ou passerelle reverse proxy: probable et coherent avec
  `uvicorn`, `SECRET_PATH`, bearer auth et la documentation, mais non nomme
  explicitement dans le depot

Inference:

Le projet cible d'abord des clients MCP distants en HTTP Streamable plutot
qu'un mode local `stdio`.

## Indices utiles pour la migration 2026-07-28

- Le serveur est deja en `stateless_http=True`, ce qui est favorable a une
  migration vers des requetes HTTP plus autonomes.
- La protection transport est centralisee:
  - bearer auth middleware
  - `TransportSecuritySettings`
  - mode global `READ_ONLY`
  - profils d'acces par token
  - rate limiting et garde-fous centraux
- La logique metier reste fortement concentree dans [server.py](/home/wnx/personal-projects/mcp-hub/server.py),
  meme si certaines fonctions sont deja extraites vers `tools/` et `core/`.

## Etat de l'environnement d'audit

- Python systeme: `3.12.3`
- Branche Git: `main`
- Commit audite: `7e01c08`
- Working tree: non propre a cause de [docs/roadmap2026-07-28.md](/home/wnx/personal-projects/mcp-hub/docs/roadmap2026-07-28.md) non suivi

## Risques connus

- Le transport `stdio` n'est pas confirme; il faut lever cette ambiguite avant
  de promettre sa preservation dans le plan de migration.
- L'environnement Python systeme n'a pas `mcp` installe, donc certaines
  commandes de baseline donnees par la roadmap echouent si elles ne passent pas
  par le virtualenv ou un environnement package.
- Le virtualenv present permet d'importer `mcp`, mais ne fournit pas `pip` ni
  `pytest` comme modules executables, ce qui complique les commandes standard
  de diagnostic et d'upgrade.
- [server.py](/home/wnx/personal-projects/mcp-hub/server.py) reste volumineux et concentre a la fois transport,
  auth, securite, enveloppes de reponse et outils, ce qui augmente le risque
  de regression lors d'une mise a jour de SDK.

## Verifications effectuees

- `git status --short --branch`
- `python3 --version`
- `python3 -m pip show mcp` -> echec, paquet absent dans l'environnement systeme
- `.venv/bin/python -c "import importlib.metadata as md; ..."` -> versions
  confirmees
- `.venv/bin/python -m unittest discover -s tests -q` -> 55 tests OK
- `.venv/bin/python -c "import asyncio, server; ..."` -> 103 tools enregistres
