#!/usr/bin/env bash
# Cloud Agent start phase: per-boot reconciliation of runtime services.
# Runs on every boot (services/daemons do not survive from the snapshot).
# Idempotent: safe if services are already up. Returns once infra is ready;
# the backend and frontend dev servers run as `terminals`.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/services.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/database.sh"

ensure_dockerd
ensure_infra

# Apply any migrations introduced on the current branch (no-op when at head).
# Seed data persists in the postgres docker volume captured by the snapshot.
echo "== [start] alembic upgrade head =="
cursor_database_dev "$CURSOR_UV_BIN" run --no-sync alembic upgrade head

ensure_localias

echo "== [start] dev infrastructure ready =="
