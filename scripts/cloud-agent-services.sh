#!/usr/bin/env bash
#
# Bring up the local data services the app depends on, idempotently.
# Used by both the install script (one-time setup) and the start script
# (per-boot reconciliation). Native Postgres/Redis/Mailpit stand in for the
# repo's docker-compose services, which need a Docker daemon not available in
# Cloud Agent VMs.
set -euo pipefail

log() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }

# --- Postgres (role `root` / db `development`, matching docker-compose.yml) ---
log "Starting PostgreSQL 16"
sudo pg_ctlcluster 16 main start 2>/dev/null || \
  sudo pg_ctlcluster 16 main restart 2>/dev/null || true

# Wait for the socket to accept connections.
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q; then break; fi
  sleep 1
done

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='root'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE ROLE root WITH LOGIN SUPERUSER PASSWORD 'password';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='development'" | grep -q 1 || \
  sudo -u postgres createdb -O root development

# --- Redis ---
log "Starting Redis"
if ! redis-cli -p 6379 ping >/dev/null 2>&1; then
  sudo redis-server --daemonize yes --port 6379
fi

# --- Mailpit (SMTP :1025, web UI :8025) ---
log "Starting Mailpit"
if ! curl -sf -o /dev/null http://localhost:8025/ 2>/dev/null; then
  mkdir -p "$HOME/.local/share/mailpit"
  nohup mailpit --smtp 0.0.0.0:1025 --listen 0.0.0.0:8025 \
    --db-file "$HOME/.local/share/mailpit/mailpit.db" \
    > "$HOME/.local/share/mailpit/mailpit.log" 2>&1 &
fi

# --- localias (HTTPS proxy for *.cosma.localhost) ---
log "Configuring localias HTTPS proxy"
# Ensure the dev hostnames resolve to localhost.
if ! grep -q 'cosma.localhost' /etc/hosts; then
  sudo tee -a /etc/hosts >/dev/null <<'HOSTSEOF'
127.0.0.1 cosma.localhost
127.0.0.1 api.cosma.localhost
127.0.0.1 chat.cosma.localhost
127.0.0.1 api-test.cosma.localhost
HOSTSEOF
fi

# Start (or reload) the daemon using the repo's .localias.yaml.
( cd "$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)" && localias start >/dev/null 2>&1 ) || true

# Trust the localias root CA in the NSS db so Chromium accepts the certs.
ROOT_CRT="$HOME/.local/state/localias/caddy/pki/authorities/local/root.crt"
if [ -f "$ROOT_CRT" ]; then
  mkdir -p "$HOME/.pki/nssdb"
  certutil -d "sql:$HOME/.pki/nssdb" -N --empty-password 2>/dev/null || true
  certutil -d "sql:$HOME/.pki/nssdb" -L 2>/dev/null | grep -q 'localias-root' || \
    certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n "localias-root" -i "$ROOT_CRT" 2>/dev/null || true
fi

log "Data services ready"
