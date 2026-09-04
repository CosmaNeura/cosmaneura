#!/usr/bin/env bash
# Explicit dev/test database command boundaries for the Cloud Agent.
set -euo pipefail

CURSOR_DATABASE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURSOR_DATABASE_GUARD="${CURSOR_DATABASE_SCRIPT_DIR}/../scripts/assert_cursor_local_database.py"

cursor_database_dev() {
  CI="" direnv exec . python "${CURSOR_DATABASE_GUARD}" --mode dev -- "$@"
}

cursor_database_test() {
  CI=true direnv exec . python "${CURSOR_DATABASE_GUARD}" --mode test -- "$@"
}
