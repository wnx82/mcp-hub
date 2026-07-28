#!/bin/bash
# Start `bw serve` with a persistent unlock, for the vaultwarden secrets provider.
#
# Only needed when MCP_SECRETS_PROVIDER=vaultwarden. Run it as the same
# unprivileged user as mcp-hub, via deploy/bw-serve.service.example.
#
# Required environment (systemd EnvironmentFile=, mode 0600):
#   BW_SERVER    https://vault.example.com
#   BW_EMAIL     you@example.com
#   BW_PASSWORD  master password
set -euo pipefail

export PATH="$HOME/bin:$PATH"

: "${BW_SERVER:?BW_SERVER not set}"
: "${BW_EMAIL:?BW_EMAIL not set}"
: "${BW_PASSWORD:?BW_PASSWORD not set}"
: "${BW_SERVE_PORT:=8090}"
: "${BW_SERVE_HOST:=127.0.0.1}"

log() { echo "[$(date -Iseconds)] $*" >&2; }

# Check state before acting: `bw config server` fails if already logged in.
STATUS=$(bw status 2>/dev/null | jq -r '.status // "unknown"' || echo "unknown")
log "Initial status: $STATUS"

case "$STATUS" in
  unauthenticated|unknown)
    log "Configuring bw server: $BW_SERVER"
    bw config server "$BW_SERVER" >/dev/null
    log "Logging in as $BW_EMAIL"
    bw login "$BW_EMAIL" --passwordenv BW_PASSWORD --raw >/dev/null
    ;;
  locked|unlocked)
    log "Already logged in (server config kept), ensuring unlock"
    ;;
esac

BW_SESSION=$(bw unlock --passwordenv BW_PASSWORD --raw)
export BW_SESSION
log "Session unlocked (len=${#BW_SESSION})"

bw sync --session "$BW_SESSION" >/dev/null 2>&1
log "Vault synced"

cleanup() {
  log "Received SIGTERM, locking + logout"
  bw lock --session "$BW_SESSION" 2>/dev/null || true
  bw logout 2>/dev/null || true
  exit 0
}
trap cleanup SIGTERM SIGINT

log "Starting bw serve on $BW_SERVE_HOST:$BW_SERVE_PORT"
exec bw serve --hostname "$BW_SERVE_HOST" --port "$BW_SERVE_PORT" --session "$BW_SESSION"
