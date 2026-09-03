#!/usr/bin/env bash
# Cloud Agent install phase: idempotent dependency refresh + local DB prep.
# Runs after the repo is checked out. Safe to run repeatedly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/services.sh"

echo "== [install] ensure pinned toolchain (.tool-versions) =="
mise install

echo "== [install] create developer-local env files (gitignored, no secrets) =="
# Mirrors `just setup`, plus the two dev values the committed examples omit:
# API_BASE_URL (required at import time) and a non-secret placeholder Clerk key.
[ -f .env.local ] || cp .env.local-example .env.local
if [ ! -f .env.dev.local ]; then
  cp .env.dev.local-example .env.dev.local
  cat >> .env.dev.local <<'EOF'

# --- Cloud Agent dev overrides (no secrets) ---
# Public origin of the backend under the localias dev setup; required at import
# time by app.configuration.server, with no committed dev default.
export API_BASE_URL=https://api.cosma.localhost
# Clerk publishable keys are non-secret (shipped to the browser). Without a real
# dev key, use the same placeholder the repo's own secretless e2e build uses so
# the frontend's import-time invariant passes and public pages render.
export VITE_CLERK_PUBLIC_KEY=${VITE_CLERK_PUBLIC_KEY:-pk_test_placeholder}
export CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLIC_KEY
EOF
fi
# Test env: CI injects a real Clerk publishable key from 1Password. Provide the
# same non-secret placeholder so js_test / pytest collection does not fail on the
# import-time Clerk invariant.
if [ ! -f .env.test.local ]; then
  cat > .env.test.local <<'EOF'
# Local test overrides (no secrets). Generated for Cloud Agent setup.
export VITE_CLERK_PUBLIC_KEY=${VITE_CLERK_PUBLIC_KEY:-pk_test_placeholder}
export CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLIC_KEY
EOF
fi

echo "== [install] python deps (uv sync) =="
denv uv sync

echo "== [install] playwright chromium (for integration tests) =="
denv uv run playwright install chromium

echo "== [install] javascript deps (pnpm) =="
denv bash -c 'cd web && pnpm install --frozen-lockfile && pnpm run typegen'

echo "== [install] bring up dev infrastructure for DB prep =="
ensure_dockerd
ensure_infra

echo "== [install] database migrate (dev + test) =="
denv just db_migrate

# Seed only when the dev database is empty. seed.py anchors are idempotent, but
# it also appends synthetic sample appointments per run, so guard for idempotence.
seeded="$(denv psql "$DATABASE_URL" -tAc \
  "SELECT COALESCE((SELECT count(*) FROM doctor), 0)" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [ "${seeded:-0}" = "0" ]; then
  echo "== [install] seeding database (dev + test) =="
  denv just db_seed
else
  echo "== [install] database already seeded (doctor rows present); skipping seed =="
fi

echo "== [install] generate OpenAPI client + route types =="
denv just js_generate-openapi

echo "== [install] done =="
