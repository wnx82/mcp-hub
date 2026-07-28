# TODO

## Audit prioritaire

- [x] Corriger le script `deploy/install.sh` pour embarquer `_version.py` dans l'installation systemd
- [x] Ajouter un test CI qui valide explicitement le flux `deploy/install.sh` sur une arborescence temporaire
- [x] Ajouter un smoke test packaging qui verifie `mcp-hub --version` et l'import du module apres installation
- [x] Decouper progressivement `server.py` en modules `tools/*` pour reduire le risque de regression dans le monolithe
- [x] Uniformiser les docstrings encore en francais vers l'anglais pour garder une interface modele coherente
- [x] Ajouter des tests cibles pour les helpers critiques: redaction, garde `READ_ONLY`, chargement de config et inventaire YAML
- [x] Ajouter une verification automatique entre les tools exposes et la documentation du README pour eviter les derives

## Audit secondaire

- [x] Documenter plus clairement la strategie de deploiement supportee: package Python, execution directe, ou installation systemd
- [x] Ajouter une note de maintenance sur la politique de dependances: `requirements.txt` epingle vs `pyproject.toml` a plage de versions
- [ ] Nettoyer les artefacts locaux du depot avant release (`__pycache__`, cache Ruff) si necessaire via `.gitignore` et checks
- [ ] Ajouter un mini jeu de fixtures de config de test pour verifier les exemples sans toucher a un vrai homelab

## Securite, fiabilite et extensibilite

- [ ] Ajouter des profils d'acces par token avec restrictions par tool, host ou tag et niveaux (`read`, `operate`, `admin`); aujourd'hui, un token donne acces a tout, comme documente dans [SECURITY.md](SECURITY.md)
- [ ] Generaliser le mode planification et confirmation temporaire des actions mutatrices, sur le modele de `destroy_resource`, notamment pour Cloudflare, DSM, Notion, les services et les ecritures de fichiers
- [ ] Ajouter les annotations MCP de comportement (`readOnlyHint`, `destructiveHint`, `idempotentHint`) afin que le client distingue les consultations des operations risquees
- [ ] Uniformiser les reponses des tools avec une enveloppe commune (`ok`, `data`, `error`, `duration_ms`, `host`, `request_id`) pour fiabiliser les raisonnements et les enchainements automatiques
- [ ] Enrichir l'audit SQLite existant dans [server.py](server.py) avec l'identite du token/client, un identifiant de requete, le resultat et un export JSON
- [ ] Ajouter des protections contre les boucles et l'epuisement des ressources: limites de concurrence par host, quotas par token, tailles maximales, circuit breaker et temporisation entre operations mutatrices
- [ ] Capturer l'etat avant les mutations Cloudflare, Notion, de configuration ou de fichier, puis proposer un tool `rollback_change`
- [ ] Ajouter des playbooks guides comme `diagnose_service`, `diagnose_endpoint`, `audit_host` et `check_backup_chain`, avec une phase d'observation avant toute correction
- [ ] Deplacer progressivement chaque domaine dans des modules comme `tools/ssh.py`, `tools/cloudflare.py` et `tools/dsm.py`, relies par un registre commun
- [ ] Une fois le socle consolide, evaluer les integrations Prometheus/Grafana, Home Assistant, Kubernetes, ntfy/Apprise et Restic/Borg

## Automatisation Claude

- [ ] Creer un skill Claude pour mettre a jour MCP Hub en securite: verification du working tree et du fast-forward, mise a jour des dependances si necessaire, redemarrage via une unite systemd transitoire, controles de sante et rollback automatique

## MCP Hub Rescue

### Architecture et isolation

- [x] Creer un composant `rescue/` autonome qui n'importe jamais `server.py`, `tools/*` ni les integrations optionnelles, directement ou transitivement
- [x] Garder le moteur Rescue et sa CLI en bibliotheque standard Python uniquement, afin que `mcp-hub-rescue doctor` fonctionne meme si les dependances du hub sont cassees
- [ ] Separer le moteur/CLI Rescue de sa facade MCP facultative; executer cette facade dans un processus, un environnement Python et une unite systemd distincts
- [x] Ajouter un test d'architecture qui echoue si `rescue/` importe `server`, `tools`, `mcp`, `httpx`, `yaml` ou une integration du hub
- [ ] Organiser Rescue en modules minimaux: `health.py`, `diagnose.py`, `repair.py`, `rollback.py`, `audit.py` et `cli.py`
- [x] Ajouter le script `mcp-hub-rescue` au packaging
- [ ] Ajouter `mcp-hub-rescue-server` au packaging si la facade MCP est activee

### Diagnostic sans mutation

- [ ] Implementer `mcp-hub-rescue status` pour retourner l'etat systemd, le PID, l'uptime, la version, la derniere erreur, l'endpoint MCP et l'etat de la configuration
- [ ] Implementer `mcp-hub-rescue health` pour verifier le service, le processus, l'endpoint, les imports essentiels, la configuration et l'espace disque
- [x] Implementer `mcp-hub-rescue logs` avec 50 lignes par defaut, une limite maximale stricte et une lecture bornee du journal
- [x] Implementer `mcp-hub-rescue validate-config` sans importer MCP Hub, avec fichier, probleme et ligne lorsque le parseur isole est disponible
- [x] Implementer un premier `mcp-hub-rescue diagnose` et `doctor` read-only pour analyser systemd, logs, Python, dependances et configuration
- [ ] Completer `diagnose` avec les permissions, les fichiers manquants et des suggestions plus precises
- [x] Ajouter un `request_id` aux resultats structures existants

### Operations controlees

- [ ] Ajouter des actions explicites `start`, `stop` et `restart` limitees a `mcp-hub.service`, suivies automatiquement d'un health check
- [x] Interdire tout shell arbitraire dans Rescue et n'exposer que des operations allowlistees avec arguments valides
- [ ] Implementer `repair` selon le flux observer, diagnostiquer, proposer un plan, confirmer, appliquer, redemarrer, verifier et rollback si necessaire
- [ ] Ajouter une confirmation temporaire et liee au plan pour toute reparation ou mutation importante
- [ ] Ajouter un mode `--safe-mode` au hub principal avec noyau et outils de diagnostic uniquement, sans integration optionnelle ni plugin
- [ ] Ajouter `restore-last-valid-config` avec validation avant restauration, permissions preservees et retention limitee des sauvegardes

### Last known good et rollback

- [ ] Enregistrer un manifeste last-known-good apres chaque health check reussi: commit Git, version, empreinte de configuration, dependances et metadonnees de deploiement
- [ ] Implementer `mcp-hub-rescue rollback` vers le dernier commit sain, avec restauration controlee des dependances puis redemarrage et health check
- [ ] Journaliser l'ancienne et la nouvelle version, les actions executees, le resultat du health check et tout rollback
- [ ] Faire passer les mises a jour par Rescue: pre-check, snapshot, fast-forward, dependances, restart, health check, validation ou rollback automatique
- [ ] Ne jamais modifier, ecraser ou committer `.env`, `hosts.yaml`, `topology.yaml`, `endpoints.yaml`, `state.db` ou `/etc/default/mcp-hub`

### Deploiement et securite

- [ ] Ajouter `deploy/mcp-hub-rescue.service.example` avec utilisateur et token distincts, acces local par defaut et durcissement systemd
- [x] Installer Rescue separement du hub principal afin qu'une mise a jour ou un virtualenv casse du hub ne remplace pas son runtime
- [ ] Ajouter un timer systemd leger pour les controles periodiques plutot qu'un daemon de monitoring supplementaire
- [ ] Configurer `StartLimitIntervalSec` et `StartLimitBurst` sur le hub principal pour eviter les boucles de redemarrage tout en laissant Rescue disponible
- [ ] Journaliser chaque action Rescue avec date, `request_id`, action, resultat, versions et rollback, puis configurer une rotation bornee
- [ ] Documenter l'authentification Rescue, le token distinct, les permissions minimales et l'interdiction d'exposition directe a Internet

### Verification

- [ ] Ajouter une suite CI Rescue couvrant: hub absent, `server.py` invalide, dependance manquante, configuration invalide, service arrete, restart, rollback et last-known-good
- [x] Tester explicitement que Rescue demarre lorsque l'import du serveur principal leve une exception
- [ ] Tester les echecs pendant chaque etape d'une mise a jour et verifier le rollback automatique sans toucher aux secrets ni aux configurations locales
- [ ] Mesurer et documenter le temps de demarrage, la memoire au repos, le nombre de dependances et la taille installee de Rescue
- [ ] Definir comme critere d'acceptation: le hub principal peut etre totalement casse, mais `mcp-hub-rescue doctor` explique la panne et propose une voie de recuperation

## Idees utiles reperees dans `bjeans/homelab-mcp`

- [ ] Ajouter un `PROJECT_INSTRUCTIONS.example.md` prive pour aider l'utilisateur a decrire sa topologie, ses hosts et ses usages MCP dans les instructions de son assistant
- [ ] Ajouter un script de verification securite lanceable localement avant publication, puis proposer un hook git pre-push optionnel
- [ ] Ajouter une documentation "testing local" pour valider les tools MCP avant release ou PR
- [ ] Etudier un packaging Docker officiel avec utilisateur non-root, healthcheck et variables d'environnement explicites
- [ ] Ajouter des hints de comportement MCP sur les tools quand FastMCP le permet (`readOnlyHint`, `idempotentHint`, `destructiveHint`, etc.)
- [ ] Standardiser progressivement les noms de tools pour rendre les retours plus predictibles (`list_*`, `get_*`, actions explicites)
- [ ] Extraire peu a peu des helpers communs de gestion d'erreur et de config pendant le decoupage du monolithe

## Documentation

- [ ] Ajouter une demo GIF ou un asciinema montrant une vraie session de troubleshooting
- [ ] Ecrire un exemple complet de connexion depuis un client MCP comme Claude Desktop ou Claude Code
- [ ] Ajouter une table de reference complete des variables d'environnement
- [ ] Etendre la section "Tool reference" avec un tableau par groupe et les signatures des outils

## Configuration et onboarding

- [ ] Verifier que les fichiers `.example` couvrent bien toutes les options documentees dans le README
- [ ] Ajouter un exemple minimal mais realiste de `hosts.yaml`
- [ ] Ajouter un exemple d'usage de `topology.yaml` pour illustrer le mapping des guests et les protections "do-not-touch"
- [ ] Ajouter un exemple de `endpoints.yaml` pour montrer comment configurer `endpoints_health`

## Qualite produit

- [x] Confirmer dans le README que le nombre de tools annonce ("85 tools") reste exact au fil des releases
- [ ] Ajouter une section de parcours types ("diagnostiquer un service", "verifier un tunnel", "agir en read-only puis lever la protection")
