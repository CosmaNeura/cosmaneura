#!/usr/bin/env bash
# Backend dev server (FastAPI/uvicorn with autoreload) on PYTHON_SERVER_PORT.
# Reachable directly on http://localhost:8205 and via https://api.cosma.localhost.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/services.sh"

# Self-heal: the backend needs Postgres + Redis. `start` normally brings these
# up on boot, but ensure them here too so this terminal is self-sufficient.
ensure_dockerd
ensure_infra

exec "$CURSOR_MISE_BIN" exec -- \
  direnv exec . env PORT="${PYTHON_SERVER_PORT:-8205}" python main.py
