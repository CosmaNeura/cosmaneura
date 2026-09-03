#!/usr/bin/env bash
# Frontend dev server (React Router 7 / Vite) on JAVASCRIPT_SERVER_PORT.
# Reachable directly on http://localhost:8207 and via https://cosma.localhost.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
exec direnv exec . just js_dev
