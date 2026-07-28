#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hook_path="$repo_root/.git/hooks/pre-push"

cat >"$hook_path" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

python3 scripts/check_repo_hygiene.py
python3 scripts/check_security_readiness.py
HOOK

chmod +x "$hook_path"
echo "Installed pre-push hook at $hook_path"
