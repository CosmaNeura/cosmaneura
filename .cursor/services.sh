#!/usr/bin/env bash
# Runtime service helpers shared by install.sh and start.sh.
# Brings up the local dev infrastructure the app depends on:
#   - Docker daemon (nested; uses fuse-overlayfs to handle overlay whiteouts)
#   - Postgres / Redis / mailpit via docker compose
#   - localias reverse proxy for https://*.cosma.localhost routing + local TLS
# All idempotent and safe to re-run on every boot.

CURSOR_DOCKER_BIN="/usr/bin/docker"
CURSOR_DOCKER_HOST="unix:///var/run/docker.sock"
CURSOR_DOCKER_SOCKET="/var/run/docker.sock"
CURSOR_LOCALIAS_BIN="/usr/local/bin/localias"
CURSOR_LOCALIAS_SHA256="e7e8ae3a6cf2b1d7b637b6d871ea615b66509096f35ed8f4cc2c370a812193aa"

docker_local() {
  /usr/bin/env \
    -u DOCKER_API_VERSION \
    -u DOCKER_CERT_PATH \
    -u DOCKER_CONTEXT \
    -u DOCKER_HOST \
    -u DOCKER_TLS_VERIFY \
    "$CURSOR_DOCKER_BIN" --host "$CURSOR_DOCKER_HOST" "$@"
}

docker_local_as_root() {
  sudo /usr/bin/env -i \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    "$CURSOR_DOCKER_BIN" --host "$CURSOR_DOCKER_HOST" "$@"
}

require_exact_localias_binary() {
  local digest metadata

  if [[ -L "$CURSOR_LOCALIAS_BIN" || ! -f "$CURSOR_LOCALIAS_BIN" || ! -x "$CURSOR_LOCALIAS_BIN" ]]; then
    echo "Localias must be a real executable at $CURSOR_LOCALIAS_BIN." >&2
    return 1
  fi
  metadata="$(/usr/bin/stat -c '%u:%a' -- "$CURSOR_LOCALIAS_BIN")"
  if [[ "$metadata" != "0:755" ]]; then
    echo "Localias must be root-owned with mode 0755." >&2
    return 1
  fi
  digest="$(/usr/bin/sha256sum -- "$CURSOR_LOCALIAS_BIN")"
  digest="${digest%% *}"
  if [[ "$digest" != "$CURSOR_LOCALIAS_SHA256" ]]; then
    echo "Localias does not match the reviewed digest." >&2
    return 1
  fi
}

secure_docker_socket_for_current_user() {
  local socket_gid socket_metadata

  if [[ -L "$CURSOR_DOCKER_SOCKET" || ! -S "$CURSOR_DOCKER_SOCKET" ]]; then
    echo "Docker did not create the expected real Unix socket." >&2
    return 1
  fi
  socket_gid="$(/usr/bin/id -g)"
  sudo /usr/bin/chown "root:${socket_gid}" -- "$CURSOR_DOCKER_SOCKET"
  sudo /usr/bin/chmod 0660 -- "$CURSOR_DOCKER_SOCKET"
  socket_metadata="$(/usr/bin/stat -c '%u:%g:%a' -- "$CURSOR_DOCKER_SOCKET")"
  if [[ "$socket_metadata" != "0:${socket_gid}:660" ]]; then
    echo "Docker socket ownership or permissions are not narrowly scoped." >&2
    return 1
  fi
}

require_live_docker_configuration() {
  local storage_driver

  storage_driver="$(docker_local info --format '{{.Driver}}')" || return 1
  if [[ "$storage_driver" != "fuse-overlayfs" ]]; then
    echo "Running Docker daemon is not using the required fuse-overlayfs driver." >&2
    return 1
  fi
}

ensure_dockerd() {
  if docker_local_as_root info >/dev/null 2>&1; then
    secure_docker_socket_for_current_user
    require_live_docker_configuration
    docker_local info >/dev/null 2>&1
    return 0
  fi
  echo "[services] starting dockerd..."
  # Cloud Agent VMs have no systemd service manager; run the daemon directly.
  # The redirect intentionally belongs to the credential-neutral user; the
  # privileged daemon does not need write access to any additional path.
  # shellcheck disable=SC2024
  sudo /usr/bin/nohup /usr/bin/dockerd --group "$(/usr/bin/id -gn)" >/tmp/dockerd.log 2>&1 &
  for _ in $(/usr/bin/seq 1 30); do
    if docker_local_as_root info >/dev/null 2>&1; then break; fi
    /usr/bin/sleep 1
  done
  secure_docker_socket_for_current_user
  require_live_docker_configuration
  docker_local info >/dev/null 2>&1 || {
    echo "[services] dockerd failed to start"
    /usr/bin/tail -20 /tmp/dockerd.log
    return 1
  }
}

ensure_infra() {
  echo "[services] bringing up postgres/redis/mailpit..."
  denv /usr/bin/env \
    -u DOCKER_API_VERSION \
    -u DOCKER_CERT_PATH \
    -u DOCKER_CONTEXT \
    -u DOCKER_HOST \
    -u DOCKER_TLS_VERIFY \
    "$CURSOR_DOCKER_BIN" --host "$CURSOR_DOCKER_HOST" compose \
    --project-directory "$REPO_ROOT" \
    --file "$REPO_ROOT/docker-compose.yml" \
    --file "$REPO_ROOT/.cursor/docker-compose.yml" \
    up -d --wait
}

run_localias_as_root() {
  sudo /usr/bin/env -i \
    "HOME=${HOME:?}" \
    "USER=${USER:?}" \
    "LOGNAME=${LOGNAME:?}" \
    "XDG_CONFIG_HOME=${XDG_CONFIG_HOME:?}" \
    "XDG_STATE_HOME=${XDG_STATE_HOME:?}" \
    "TMPDIR=${TMPDIR:?}" \
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$CURSOR_LOCALIAS_BIN" "$@"
}

ensure_localias() {
  require_exact_localias_binary
  local localias_root_crt
  # The pinned Localias source defines CaddyStatePath as
  # xdg.StateFile("localias/caddy") and appends this fixed PKI path.
  localias_root_crt="${XDG_STATE_HOME:?}/localias/caddy/pki/authorities/local/root.crt"

  if run_localias_as_root status 2>/dev/null | /usr/bin/grep -q "daemon running"; then
    echo "[services] localias already running"
  else
    echo "[services] starting localias..."
    run_localias_as_root start
  fi

  if [[ -L "$localias_root_crt" || ! -f "$localias_root_crt" ]]; then
    echo "Localias did not create its CA at the exact isolated state path." >&2
    return 1
  fi
  /usr/bin/openssl x509 -in "$localias_root_crt" -noout >/dev/null

  # Chromium trusts certificates from the credential-neutral runtime HOME's
  # dedicated NSS database. Replace the nickname on every run so a legitimate
  # Localias CA rotation cannot leave a stale certificate trusted.
  /bin/mkdir -p "$HOME/.pki/nssdb"
  if [[ ! -f "$HOME/.pki/nssdb/cert9.db" ]]; then
    /usr/bin/certutil -d "sql:$HOME/.pki/nssdb" -N --empty-password </dev/null
  fi
  if /usr/bin/certutil -d "sql:$HOME/.pki/nssdb" -L -n localias-root >/dev/null 2>&1 </dev/null; then
    /usr/bin/certutil -d "sql:$HOME/.pki/nssdb" -D -n localias-root </dev/null
  fi
  /usr/bin/certutil -d "sql:$HOME/.pki/nssdb" -A -t "C,," -n localias-root \
    -i "$localias_root_crt" </dev/null
}
