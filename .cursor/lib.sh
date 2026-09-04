#!/usr/bin/env bash
# Common bootstrap for Cloud Agent environment scripts.
# Resolves the mise-managed toolchain through one digest-verified Mise binary
# and prepares direnv, which loads non-secret env config from .env.* files.
# No secrets are read or written here.
set -euo pipefail

CURSOR_LIB_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$CURSOR_LIB_DIR/credential-neutral.sh"
cursor_require_credential_neutral

CURSOR_MISE_BIN="$HOME/.local/bin/mise"
cursor_require_exact_mise_binary "$CURSOR_MISE_BIN"

CURSOR_DIRENV_BIN="$(cursor_mise_executable direnv)"
CURSOR_JUST_BIN="$(cursor_mise_executable just)"
CURSOR_NODE_BIN="$(cursor_mise_executable node)"
CURSOR_PNPM_BIN="$(cursor_mise_executable pnpm)"
CURSOR_PYTHON_BIN="$(cursor_mise_executable python)"
CURSOR_UV_BIN="$(cursor_mise_executable uv)"

# Resolve repo root (parent of this .cursor dir) and work from there.
REPO_ROOT="$(cd -P "$CURSOR_LIB_DIR/.." && pwd -P)"
cd -P "$REPO_ROOT"

# direnv drives all environment configuration via .envrc + .env.* files.
# Allow it non-interactively so `direnv exec .` works in scripts.
"$CURSOR_MISE_BIN" exec -- "$CURSOR_DIRENV_BIN" allow . >/dev/null 2>&1

# Resolve every managed tool through the exact reviewed Mise binary. Its
# generated shim directory is intentionally never placed on PATH.
denv() {
  "$CURSOR_MISE_BIN" exec -- "$CURSOR_DIRENV_BIN" exec . \
    /usr/bin/env UV_PYTHON="$CURSOR_PYTHON_BIN" UV_PYTHON_DOWNLOADS=never "$@"
}
