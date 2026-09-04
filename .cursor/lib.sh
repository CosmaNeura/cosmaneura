#!/usr/bin/env bash
# Common bootstrap for Cloud Agent environment scripts.
# Puts the mise-managed toolchain (uv, node, pnpm, just, direnv, python) on PATH
# and prepares direnv, which loads all non-secret env config from .env.* files.
# No secrets are read or written here.
set -euo pipefail

CURSOR_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$CURSOR_LIB_DIR/credential-neutral.sh"
cursor_require_credential_neutral

export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

# Activate the mise-pinned tool versions from .tool-versions.
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate bash --shims)" 2>/dev/null || true
fi
export PATH="$CURSOR_LIB_DIR/credential-neutral-bin:$PATH"

# Resolve repo root (parent of this .cursor dir) and work from there.
REPO_ROOT="$(cd "$CURSOR_LIB_DIR/.." && pwd)"
cd "$REPO_ROOT"

# direnv drives all environment configuration via .envrc + .env.* files.
# Allow it non-interactively so `direnv exec .` works in scripts.
if command -v direnv >/dev/null 2>&1; then
  direnv allow . >/dev/null 2>&1 || true
fi

# Run a command with the project's direnv-loaded environment.
denv() { direnv exec . "$@"; }
