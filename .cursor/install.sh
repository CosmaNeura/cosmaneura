#!/usr/bin/env bash
# Cloud Agent install phase: idempotent dependency refresh + local DB prep.
# Runs after the repo is checked out. Safe to run repeatedly.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/credential-neutral.sh"
cursor_require_credential_neutral

# Provision OS-level dependencies first (installs mise, which lib.sh needs).
echo "== [install] system provisioning =="
bash "$SCRIPT_DIR/system-setup.sh"

echo "== [install] ensure pinned toolchain (.tool-versions) =="
CURSOR_MISE_BIN="$HOME/.local/bin/mise"
cursor_require_exact_mise_binary "$CURSOR_MISE_BIN"
"$CURSOR_MISE_BIN" install

# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/services.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/database.sh"

echo "== [install] use committed credential-neutral environment defaults =="

echo "== [install] python deps (uv sync) =="
denv "$CURSOR_UV_BIN" sync --locked

echo "== [install] playwright chromium (for integration tests) =="
denv "$CURSOR_UV_BIN" run --no-sync playwright install chromium

echo "== [install] javascript deps (pnpm) =="
# shellcheck disable=SC2016
denv /usr/bin/env PATH="${CURSOR_CREDENTIAL_SCRIPT_DIR}/credential-neutral-bin:$(/usr/bin/dirname "$CURSOR_NODE_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  /bin/bash -c 'cd web && "$1" install --frozen-lockfile && "$1" run typegen' \
  _ "$CURSOR_PNPM_BIN"

echo "== [install] bring up dev infrastructure for DB prep =="
ensure_dockerd
ensure_infra

mkdir -p migrations/versions

echo "== [install] ensure local test database exists =="
if ! test_database_exists="$(
  # shellcheck disable=SC2016
  cursor_database_dev /bin/bash -c \
    '/usr/bin/psql "$DATABASE_URL" -tAc "SELECT 1 FROM pg_database WHERE datname = '\''test'\''"' \
    2>/dev/null | /usr/bin/tr -d '[:space:]'
)"; then
  echo "Unable to inspect the local test database; refusing database setup." >&2
  exit 1
fi
case "$test_database_exists" in
  "")
    # shellcheck disable=SC2016
    cursor_database_dev /bin/bash -c \
      '/usr/bin/psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE test"'
    ;;
  1) ;;
  *)
    echo "Unexpected local test database existence result; refusing database setup." >&2
    exit 1
    ;;
esac

echo "== [install] migrate local development database =="
cursor_database_dev "$CURSOR_UV_BIN" run --no-sync alembic upgrade head

echo "== [install] migrate local test database =="
cursor_database_test "$CURSOR_UV_BIN" run --no-sync alembic upgrade head

database_doctor_count() {
  local mode="$1"
  local count
  if [[ "$mode" == "dev" ]]; then
    if ! count="$(
      # shellcheck disable=SC2016
      cursor_database_dev /bin/bash -c \
        '/usr/bin/psql "$DATABASE_URL" -tAc "SELECT COALESCE((SELECT count(*) FROM doctor), 0)"' \
        2>/dev/null | /usr/bin/tr -d '[:space:]'
    )"; then
      echo "Unable to inspect the local development seed state; refusing to seed." >&2
      return 1
    fi
  elif [[ "$mode" == "test" ]]; then
    if ! count="$(
      # shellcheck disable=SC2016
      cursor_database_test /bin/bash -c \
        '/usr/bin/psql "$TEST_DATABASE_URL" -tAc "SELECT COALESCE((SELECT count(*) FROM doctor), 0)"' \
        2>/dev/null | /usr/bin/tr -d '[:space:]'
    )"; then
      echo "Unable to inspect the local test seed state; refusing to seed." >&2
      return 1
    fi
  else
    echo "Unknown local database mode; refusing to seed." >&2
    return 1
  fi
  if [[ ! "$count" =~ ^[0-9]+$ ]]; then
    echo "Unexpected local database seed-state result; refusing to seed." >&2
    return 1
  fi
  printf '%s' "$count"
}

dev_doctor_count="$(database_doctor_count dev)"
if [[ "$dev_doctor_count" == "0" ]]; then
  echo "== [install] seed local development database =="
  cursor_database_dev "$CURSOR_UV_BIN" run --no-sync python migrations/seed.py
else
  echo "== [install] development database already seeded; skipping seed =="
fi

test_doctor_count="$(database_doctor_count test)"
if [[ "$test_doctor_count" == "0" ]]; then
  echo "== [install] seed local test database =="
  cursor_database_test "$CURSOR_UV_BIN" run --no-sync python migrations/seed.py
else
  echo "== [install] test database already seeded; skipping seed =="
fi

echo "== [install] use committed OpenAPI client (regeneration remains explicit) =="

echo "== [install] done =="
