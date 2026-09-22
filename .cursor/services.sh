#!/usr/bin/env bash
# Runtime service helpers shared by install.sh and start.sh.
# Brings up the local dev infrastructure the app depends on:
#   - Docker daemon (nested; uses fuse-overlayfs to handle overlay whiteouts)
#   - Postgres / Redis / mailpit via docker compose
#   - localias reverse proxy for https://*.cosma.localhost routing + local TLS
# All idempotent and safe to re-run on every boot.

ensure_dockerd() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  echo "[services] starting dockerd..."
  # Cloud Agent VMs have no systemd service manager; run the daemon directly.
  sudo nohup dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 30); do
    if sudo docker info >/dev/null 2>&1; then break; fi
    sleep 1
  done
  # Let the non-root user talk to the daemon without sudo.
  sudo chmod 666 /var/run/docker.sock 2>/dev/null || true
  docker info >/dev/null 2>&1 || { echo "[services] dockerd failed to start"; tail -20 /tmp/dockerd.log; return 1; }
}

ensure_infra() {
  echo "[services] bringing up postgres/redis/mailpit..."
  denv /usr/bin/docker compose \
    --project-directory "$REPO_ROOT" \
    --file "$REPO_ROOT/docker-compose.yml" \
    --file "$REPO_ROOT/.cursor/docker-compose.yml" \
    up -d --wait
}

ensure_localias() {
  if sudo -E localias status 2>/dev/null | grep -q "daemon running"; then
    echo "[services] localias already running"
  else
    echo "[services] starting localias..."
    sudo -E localias start
  fi
  # Ensure the localias root CA is trusted by the system and by Chromium (NSS),
  # so the browser can load https://*.cosma.localhost without warnings.
  # `|| true` guards against SIGPIPE from head closing the find pipeline early
  # (lib.sh enables pipefail + set -e).
  local root_crt
  root_crt="$(sudo find "$HOME/.local/state/localias" /root/.local/state/localias \
    -name root.crt -path '*authorities/local*' 2>/dev/null | head -1 || true)"
  if [ -n "${root_crt:-}" ]; then
    sudo cp "$root_crt" /usr/local/share/ca-certificates/localias-root.crt </dev/null
    sudo update-ca-certificates >/dev/null 2>&1 </dev/null || true
    # Chromium trusts certs from the NSS db. Initialize it only when missing;
    # `certutil -N` prompts (and would hang) if the db already exists. Redirect
    # stdin from /dev/null throughout so nothing can block on a prompt.
    mkdir -p "$HOME/.pki/nssdb"
    if [ ! -f "$HOME/.pki/nssdb/cert9.db" ]; then
      certutil -d "sql:$HOME/.pki/nssdb" -N --empty-password >/dev/null 2>&1 </dev/null || true
    fi
    if ! certutil -d "sql:$HOME/.pki/nssdb" -L </dev/null 2>/dev/null | grep -q localias-root; then
      certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n localias-root -i "$root_crt" >/dev/null 2>&1 </dev/null || true
    fi
  fi
}
