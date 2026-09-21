#!/usr/bin/env bash
# Explicit dev/test database command boundaries for the Cloud Agent.
set -euo pipefail

CURSOR_DATABASE_SCRIPT_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CURSOR_DATABASE_GUARD="${CURSOR_DATABASE_SCRIPT_DIR}/../scripts/assert_cursor_local_database.py"

cursor_database_dev() {
  CI="" "${CURSOR_MISE_BIN:?}" exec -- \
    "${CURSOR_DIRENV_BIN:?}" exec . /usr/bin/env \
    UV_PYTHON="${CURSOR_PYTHON_BIN:?}" UV_PYTHON_DOWNLOADS=never "${CURSOR_PYTHON_BIN}" \
    "${CURSOR_DATABASE_GUARD}" --mode dev -- "$@"
}

cursor_database_test() {
  CI=true "${CURSOR_MISE_BIN:?}" exec -- \
    "${CURSOR_DIRENV_BIN:?}" exec . /usr/bin/env \
    UV_PYTHON="${CURSOR_PYTHON_BIN:?}" UV_PYTHON_DOWNLOADS=never "${CURSOR_PYTHON_BIN}" \
    "${CURSOR_DATABASE_GUARD}" --mode test -- "$@"
}
