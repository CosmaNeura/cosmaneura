#!/usr/bin/env bash
# Backend dev server (FastAPI/uvicorn with autoreload) on PYTHON_SERVER_PORT.
# Reachable directly on http://localhost:8205 and via https://api.cosma.localhost.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/services.sh"

# Self-heal: the backend needs Postgres + Redis. `start` normally brings these
# up on boot, but ensure them here too so this terminal is self-sufficient.
ensure_dockerd
ensure_infra

exec "$CURSOR_MISE_BIN" exec -- "$CURSOR_DIRENV_BIN" exec . \
  /usr/bin/env PORT="${PYTHON_SERVER_PORT:-8205}" \
  UV_PYTHON="$CURSOR_PYTHON_BIN" UV_PYTHON_DOWNLOADS=never "$CURSOR_UV_BIN" run --no-sync python main.py
