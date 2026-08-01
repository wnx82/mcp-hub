# Cartographie des dependances aux sessions

Date d'audit: 2026-08-01

## Resume

La recherche ne montre aucune dependance explicite a une session MCP de
transport telle que `Mcp-Session-Id`. En revanche, le serveur maintient
plusieurs formes d'etat applicatif ou technique hors protocole:

- identifiant de requete par appel
- plans de mutation en attente
- confirmations destructives en attente
- caches memoire de tokens et SID externes
- cache SQLite de certaines reponses
- connexions SSH multiplexees persistantes
- hote par defaut pour certaines actions

Ces mecanismes ne bloquent pas automatiquement une migration vers un protocole
MCP sans session, mais ils doivent etre explicitement distingues de l'etat de
transport pour eviter toute confusion pendant la refonte.

## Recherche effectuee

Patterns recherches:

- `Mcp-Session-Id`
- `session_id`
- `sessionId`
- `initialize`
- `initialized`
- `request_context`
- `context.session`
- `ctx.session`

Constat:

- aucune occurrence de `Mcp-Session-Id`
- aucune occurrence de `session_id`
- aucune occurrence de `sessionId`
- aucune occurrence de `request_context`, `context.session` ou `ctx.session`
- une occurrence informative autour du handshake `initialize`, sans logique
  de dependance a une initialisation prealable

## Tableau des usages

| Fichier | Ligne | Usage | Risque | Action prevue |
| ------- | ----: | ----- | ------ | ------------- |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:581) | 581 | `_current_request_id` via `contextvars` pour tracer un appel et journaliser l'enveloppe de reponse | Faible. Etat par requete, pas une session de transport | Conserver comme correlation id, sans le lier a `initialize` |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:3948) | 3948 | `_pending_mutations` conserve un plan de mutation confirme ultérieurement par token | Moyen. Etat applicatif transitoire entre deux requetes | Le documenter comme etat metier explicite, pas comme session MCP |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:3332) | 3332 | `_PENDING_DESTROY` conserve une confirmation destructive en deux etapes | Moyen. Etat transitoire memoire uniquement | Aligner ce flux avec la strategie generale d'etat explicite |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:1470) | 1470 | `_notion_token_cache` conserve un token Notion en memoire avec TTL | Faible. Cache d'integration externe, independant du transport | Conserver, mais verifier qu'aucune info de client n'y est attachee |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:2202) | 2202 | `_dsm_sid_cache` conserve un SID DSM en memoire avec TTL | Faible a moyen. "session" au sens DSM, pas MCP | Renommer/documenter clairement comme session d'integration externe |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:2960) | 2960 | table `kv` dans SQLite pour cache `topology` et assimilables | Faible. Cache local non lie au client | Garder distinct du protocole, documenter sa portee et son invalidation |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:3298) | 3298 | `topology()` lit et ecrit un cache `kv` de 600 s | Faible | Eventuellement enrichir avec metadonnees de cache MCP plus tard |
| [tools/ssh.py](/home/wnx/personal-projects/mcp-hub/tools/ssh.py:40) | 40 | OpenSSH `ControlMaster` + `ControlPersist=60m` + `ControlPath` | Moyen. Etat de connexion technique persistant entre appels | Conserver comme optimisation technique, sans hypothese de session MCP |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:3398) | 3398 | `ssh_reset_control()` ferme une connexion SSH persistante | Faible | Classer comme gestion de connexion technique |
| [config.py](/home/wnx/personal-projects/mcp-hub/config.py:199) | 199 | `DEFAULT_HYPERVISOR` sert d'hote implicite pour des tools Proxmox/Docker | Moyen. Etat implicite de ciblage, mais statique | Rendre les tools critiques plus explicites si necessaire |
| [server.py](/home/wnx/personal-projects/mcp-hub/server.py:657) | 657 | commentaire sur `initialize` pour exposer la version du hub | Faible. Pas de dependance fonctionnelle | Aucun changement requis, verifier seulement apres upgrade SDK |

## Classification par type

### Transport MCP

Aucune dependance explicite detectee a une session de transport MCP.

### Etat metier applicatif

- `_pending_mutations`
- `_PENDING_DESTROY`

Ces etats representent des workflows de confirmation en plusieurs tours.

### Cache

- `_notion_token_cache`
- `_dsm_sid_cache`
- table SQLite `kv`
- cache de `topology()`

### Securite / autorisation

- `_current_request_id` pour correlation et audit
- tokens de confirmation lies au profil d'acces courant

### Connexion technique

- connexions SSH multiplexees via `ControlMaster` / `ControlPersist`

## Conclusions pour la migration

1. La migration vers MCP `2026-07-28` ne semble pas bloquee par une ancienne
   session de transport MCP.
2. Le vrai travail porte plutot sur la clarification de l'etat applicatif
   multi-requetes, surtout les confirmations et les destructions en deux temps.
3. Le mot "session" devra etre reserve au protocole MCP ou aux integrations
   externes comme DSM, pour eviter les confusions pendant la refonte.
4. Les optimisations SSH persistantes sont orthogonales au protocole et peuvent
   rester en place si elles ne deviennent pas une source d'etat implicite cote
   client.
