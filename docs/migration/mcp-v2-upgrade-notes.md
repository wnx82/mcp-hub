# Notes de migration vers le SDK MCP v2

Date de collecte: 2026-08-01

## Conclusion courte

La version stable du SDK Python qui prend en charge la specification MCP
`2026-07-28` est `mcp v2.0.0`, publiee le 2026-07-28.

La migration n'est pas un simple bump de dependance:

- `FastMCP` devient `MCPServer`
- les parametres de transport quittent le constructeur pour `run()` et
  `streamable_http_app()`
- `pip install mcp` installe maintenant `2.x`
- la ligne `1.x` reste en maintenance, mais ne porte pas la nouvelle spec
- plusieurs comportements et API annexes changent ou sont deprecies

## Sources primaires

- SDK Python officiel: https://github.com/modelcontextprotocol/python-sdk
- Release `v2.0.0`: https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0
- Guide de migration v1 -> v2: https://py.sdk.modelcontextprotocol.io/migration/
- "What's new in v2": https://py.sdk.modelcontextprotocol.io/whats-new/
- Index SDKs MCP: https://modelcontextprotocol.io/docs/2026-07-28/sdk

## Faits verifies

### Support de la spec

Le release note `v2.0.0` indique que cette version supporte la revision
`2026-07-28` et sert aussi les revisions precedentes depuis le meme serveur.

Inference:

Pour un serveur qui veut parler nativement `2026-07-28` tout en restant
compatible avec les clients 2025 quand le SDK le permet, la cible normale est
`mcp>=2,<3`.

### Compatibilite descendante

Le meme release note de `v2.0.0` annonce que `v2` sert aussi les revisions
precedentes via Streamable HTTP et `stdio`.

Inference:

Le SDK v2 est cense couvrir l'objectif roadmap de compatibilite avec les
anciens clients mieux que la ligne `1.x`, mais cela doit etre valide sur le hub
reel apres upgrade.

### Changement d'API serveur

Le guide de migration confirme:

- `FastMCP` renomme en `MCPServer`
- les parametres transport (`host`, `port`, `json_response`,
  `stateless_http`, `streamable_http_path`, etc.) passent du constructeur vers
  `run()` et `streamable_http_app()`
- un serveur sans `version=` explicite ne rapporte plus automatiquement la
  version du package SDK

### Changement de resolution de dependance

Le README et les releases v2 precisent que `pip install mcp` installe
desormais `2.x`.

Consequence:

Les projets qui veulent rester temporairement sur v1 doivent garder une borne
superieure `<2`. MCP Hub le fait deja actuellement dans
[pyproject.toml](/home/wnx/personal-projects/mcp-hub/pyproject.toml).

### Changement de comportement protocolaire

Les documents officiels v2 indiquent notamment:

- plus de handshake obligatoire pour la nouvelle ere protocolaire
- plus de session de transport au coeur du protocole
- deprecation de roots, sampling et protocol logging
- suppression de `ping`
- multi-round-trip requests pour remplacer les requetes serveur -> client

## Adaptation deja faite dans le depot

Le bootstrap de [server.py](/home/wnx/personal-projects/mcp-hub/server.py) est maintenant
prepare a v1/v2 sans casser `mcp 1.28.1`:

- import conditionnel `MCPServer` v2 / `FastMCP` v1
- transport HTTP centralise dans un helper
- `version=__version__` prevu pour v2
- fallback v1 conserve pour l'override `mcp._mcp_server.version`

## Risques restant avant l'upgrade effectif

- il faut verifier que `TransportSecuritySettings` se branche exactement comme
  attendu dans `run()` et `streamable_http_app()` sous v2
- il faut valider que le middleware bearer actuel continue a envelopper
  proprement l'ASGI app v2
- il faut verifier si certains tests ou scripts reposent indirectement sur des
  comportements v1 de `mcp`
- il faut preparer une validation manuelle des clients reels apres upgrade

## Recommandation pour l'etape suivante

1. Relever la contrainte de dependance vers `mcp>=2,<3`.
2. Installer un environnement de test v2 propre.
3. Corriger les erreurs d'import ou de runtime restantes.
4. Rejouer les tests unitaires.
5. Verifier explicitement le transport HTTP streamable et, si necessaire, la
   promesse de compatibilite `stdio`.
