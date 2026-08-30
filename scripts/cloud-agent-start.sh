#!/usr/bin/env bash
#
# Cloud Agent start script — runs on every boot (before the agent works).
# Reconciles per-boot runtime state: starts data services and re-applies any
# pending migrations. Dependency installation lives in cloud-agent-install.sh;
# the long-running dev servers run as `terminals` (see .cursor/environment.json).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

# Start Postgres, Redis, Mailpit, and the localias HTTPS proxy.
"$REPO_ROOT/scripts/cloud-agent-services.sh"

# Ensure direnv trusts the repo, then apply migrations (idempotent).
direnv allow "$REPO_ROOT" 2>/dev/null || true
direnv exec "$REPO_ROOT" just db_migrate

printf '\033[1;32m==> Environment started. Dev servers run in the backend/frontend terminals.\033[0m\n'
