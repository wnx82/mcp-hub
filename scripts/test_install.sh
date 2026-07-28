#!/usr/bin/env bash
set -euo pipefail

staging="$(mktemp -d)"
trap 'sudo rm -rf -- "$staging"' EXIT

prefix="$staging/opt/mcp-hub"
rescue_prefix="$staging/opt/mcp-hub-rescue"
env_file="$staging/etc/default/mcp-hub"
unit_file="$staging/etc/systemd/system/mcp-hub.service"
rescue_bin="$staging/usr/local/bin/mcp-hub-rescue"

sudo install -d \
  "$(dirname "$env_file")" \
  "$(dirname "$unit_file")" \
  "$(dirname "$rescue_bin")"

run_installer() {
  sudo env \
    PREFIX="$prefix" \
    RESCUE_PREFIX="$rescue_prefix" \
    ENV_FILE="$env_file" \
    UNIT_FILE="$unit_file" \
    RESCUE_BIN="$rescue_bin" \
    SERVICE_USER=root \
    INSTALL_DEPENDENCIES=false \
    INSTALL_SSH_KEY=false \
    RELOAD_SYSTEMD=false \
    ./deploy/install.sh
}

run_installer

sudo test -f "$prefix/server.py"
sudo test -f "$prefix/config.py"
sudo test -f "$prefix/_version.py"
sudo test -f "$rescue_prefix/rescue/cli.py"
sudo test -x "$rescue_bin"
sudo test -f "$env_file"
sudo test -f "$unit_file"

sudo sh -c "printf '%s\n' '# local inventory' > '$prefix/hosts.yaml'"
sudo sh -c "printf '%s\n' '# local environment' > '$env_file'"
sudo sh -c "printf '%s\n' '# local unit' > '$unit_file'"

run_installer

test "$(sudo cat "$prefix/hosts.yaml")" = "# local inventory"
test "$(sudo cat "$env_file")" = "# local environment"
test "$(sudo cat "$unit_file")" = "# local unit"

echo "install flow and idempotence validated"
