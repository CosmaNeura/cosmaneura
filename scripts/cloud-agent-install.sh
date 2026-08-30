#!/usr/bin/env bash
#
# Cloud Agent install script (Cursor background agents / Linux).
#
# Runs once after the repository is checked out to produce a fully usable
# development baseline: system packages, the pinned mise toolchain, local data
# services (Postgres, Redis, Mailpit), the localias HTTPS proxy, Python and
# JavaScript dependencies, generated OpenAPI client, and applied DB migrations.
#
# The normal macOS `just setup` path depends on Homebrew, 1Password, and
# OrbStack, none of which exist here. This script fills that gap and is
# idempotent so it can be re-run safely.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
log "Installing system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  zsh \
  libnss3-tools \
  poppler-utils \
  ffmpeg \
  git \
  curl \
  ca-certificates \
  build-essential \
  libpq-dev \
  lsof \
  postgresql \
  postgresql-contrib \
  redis-server

# ---------------------------------------------------------------------------
# 2. mise (tool version manager) + pinned toolchain from .tool-versions
# ---------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v mise >/dev/null 2>&1; then
  log "Installing mise"
  curl -fsSL https://mise.run | sh
fi
# Persist PATH + activation for interactive shells / agent terminals.
grep -q 'mise activate bash' "$HOME/.bashrc" 2>/dev/null || \
  echo 'eval "$($HOME/.local/bin/mise activate bash)"' >> "$HOME/.bashrc"
grep -q '.local/share/mise/shims' "$HOME/.bashrc" 2>/dev/null || \
  echo 'export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"' >> "$HOME/.bashrc"

log "Installing pinned toolchain (uv, node, pnpm, just, direnv, python)"
mise trust --yes "$REPO_ROOT"
mise install
export PATH="$HOME/.local/share/mise/shims:$PATH"

# ---------------------------------------------------------------------------
# 3. Mailpit (SMTP sinkhole) — replaces the docker-compose mailpit service
# ---------------------------------------------------------------------------
if ! command -v mailpit >/dev/null 2>&1; then
  log "Installing mailpit"
  curl -fsSL https://github.com/axllent/mailpit/releases/latest/download/mailpit-linux-amd64.tar.gz \
    -o /tmp/mailpit.tar.gz
  tar -xzf /tmp/mailpit.tar.gz -C /tmp mailpit
  sudo install -m 0755 /tmp/mailpit /usr/local/bin/mailpit
fi

# ---------------------------------------------------------------------------
# 4. localias (HTTPS reverse proxy for the *.cosma.localhost dev domains)
# ---------------------------------------------------------------------------
if ! command -v localias >/dev/null 2>&1; then
  log "Installing localias"
  curl -fsSL "https://github.com/peterldowns/localias/releases/download/v3.0.0%2Bcommit.43c3619/localias-linux-amd64" \
    -o /tmp/localias
  sudo install -m 0755 /tmp/localias /usr/local/bin/localias
fi
# Allow the (non-root) agent user to bind ports 80/443.
sudo setcap 'cap_net_bind_service=+ep' /usr/local/bin/localias

# ---------------------------------------------------------------------------
# 5. Non-secret placeholder secrets (.env.dev.local)
#
# Local dev normally sources these from 1Password. Cloud Agents have no
# 1Password access, so provide inert placeholders that satisfy the app's
# import-time, fail-fast configuration. External integrations stay disabled.
# ---------------------------------------------------------------------------
if [ ! -f "$REPO_ROOT/.env.dev.local" ]; then
  log "Writing placeholder .env.dev.local"
  cat > "$REPO_ROOT/.env.dev.local" <<'ENVEOF'
# Cloud Agent / offline development overrides (NON-SECRET placeholders).
# Local dev normally sources these from 1Password; Cloud Agents cannot.
# They satisfy the app's import-time fail-fast config so every process boots.
# External integrations (Clerk, Azure, OpenAI, AssemblyAI, Recall, PostHog)
# are intentionally inert. Provide real secrets to exercise those paths.

export API_BASE_URL=http://localhost:8205

export VITE_CLERK_PUBLIC_KEY=pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk
export CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk
export CLERK_PRIVATE_KEY=sk_test_placeholder00000000000000000000000000

export ASSEMBLY_API_KEY=placeholder-assemblyai-key

export AZURE_UPLOADS_ACCOUNT_NAME=devplaceholder
export AZURE_UPLOADS_ACCOUNT_KEY=cGxhY2Vob2xkZXItYXp1cmUtdXBsb2Fkcy1rZXk=
export AZURE_UPLOADS_ACCOUNT_URL=https://devplaceholder.blob.core.windows.net
export AZURE_UPLOADS_CONTAINER_NAME=uploads
export AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME=example-notes

export AZURE_DEID_TENANT_ID=00000000-0000-0000-0000-000000000000
export AZURE_DEID_CLIENT_ID=00000000-0000-0000-0000-000000000000
export AZURE_DEID_CLIENT_SECRET=placeholder-azure-deid-secret
export AZURE_DEID_ENDPOINT=https://devplaceholder.api.deid.azure.com

export AZURE_OPENAI_API_KEY=placeholder-azure-openai-key
export AZURE_OPENAI_ENDPOINT=https://devplaceholder.openai.azure.com/

export RECALL_API_KEY=placeholder-recall-key

export POSTHOG_SECRET_KEY=phc_placeholder000000000000000000000000000000
export POSTHOG_HOST=https://us.i.posthog.com
export VITE_POSTHOG_KEY=phc_placeholder000000000000000000000000000000
export VITE_POSTHOG_HOST=https://us.i.posthog.com
ENVEOF
fi

# Test-mode overrides (loaded when CI=true). Mirrors the env the GitHub Actions
# PR workflow injects so `just js_test` / `just py_test` run without 1Password.
if [ ! -f "$REPO_ROOT/.env.test.local" ]; then
  log "Writing placeholder .env.test.local"
  cat > "$REPO_ROOT/.env.test.local" <<'ENVEOF'
# Cloud Agent test-mode overrides (NON-SECRET placeholders).
# Mirrors the env the GitHub Actions PR workflow injects for test runs.
export VITE_CLERK_PUBLIC_KEY=pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk
export CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk
ENVEOF
fi

# ---------------------------------------------------------------------------
# 6. Data services + database bootstrap (needed to import the app / migrate)
# ---------------------------------------------------------------------------
log "Bringing up data services for setup"
"$REPO_ROOT/scripts/cloud-agent-services.sh"

# direnv needs to trust the repo before `direnv exec` works.
direnv allow "$REPO_ROOT"

# ---------------------------------------------------------------------------
# 7. Python + JavaScript dependencies, OpenAPI client, migrations
# ---------------------------------------------------------------------------
log "Installing Python dependencies (uv sync + playwright)"
direnv exec "$REPO_ROOT" just py_setup

log "Applying database migrations (dev + test)"
direnv exec "$REPO_ROOT" just db_migrate

log "Installing JavaScript dependencies"
direnv exec "$REPO_ROOT" just js_setup

log "Generating OpenAPI TypeScript client"
direnv exec "$REPO_ROOT" just js_generate-openapi

log "Install complete"
echo "NOTE: 'just db_seed' is intentionally skipped — it calls the live Clerk API"
echo "      to create a seed user and requires a real CLERK_PRIVATE_KEY."
