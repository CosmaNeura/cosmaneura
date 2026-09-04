#!/usr/bin/env bash
# Frontend dev server (React Router 7 / Vite) on JAVASCRIPT_SERVER_PORT.
# Reachable directly on http://localhost:8207 and via https://cosma.localhost.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
exec "$CURSOR_MISE_BIN" exec -- "$CURSOR_DIRENV_BIN" exec . /usr/bin/env \
  PATH="${CURSOR_CREDENTIAL_SCRIPT_DIR}/credential-neutral-bin:$(/usr/bin/dirname "$CURSOR_PNPM_BIN"):$(/usr/bin/dirname "$CURSOR_JUST_BIN"):$(/usr/bin/dirname "$CURSOR_NODE_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  "$CURSOR_JUST_BIN" js_dev
