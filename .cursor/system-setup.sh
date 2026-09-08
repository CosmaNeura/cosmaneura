#!/usr/bin/env bash
# Idempotent system provisioning for the Cloud Agent base image.
# Installs the OS-level dependencies the cosmaneura dev stack needs but that the
# repo's macOS-only `just setup` does not cover on Linux:
#   - apt packages (Postgres client, ffmpeg, poppler, NSS tools, build deps, ...)
#   - Docker Engine + compose (nested; fuse-overlayfs to handle overlay whiteouts)
#   - mise (pins uv/pnpm/just/direnv/python/node via .tool-versions)
#   - localias (https://*.cosma.localhost routing + local TLS)
# Safe to re-run: every step checks before acting. No secrets.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APT_CONFOLD=(-o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef)
CURSOR_SETUP_DIR="$(cd -P "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "$CURSOR_SETUP_DIR/credential-neutral.sh"
cursor_require_credential_neutral

echo "== [system] apt packages =="
APT_PACKAGES=(
  zsh libnss3-tools poppler-utils ffmpeg
  ca-certificates curl gnupg lsb-release jq
  postgresql-client redis-tools
  libpq-dev build-essential lsof
  fuse-overlayfs
)
missing=()
for pkg in "${APT_PACKAGES[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ "${#missing[@]}" -gt 0 ]; then
  sudo apt-get "${APT_CONFOLD[@]}" -o Acquire::Retries=3 update -qq
  sudo apt-get "${APT_CONFOLD[@]}" -o Acquire::Retries=3 install -y --no-install-recommends "${missing[@]}"
fi

echo "== [system] docker engine =="
if [[ ! -x /usr/bin/docker || ! -x /usr/bin/dockerd ]] \
  || ! /usr/bin/docker compose version >/dev/null 2>&1; then
  bash "$CURSOR_SETUP_DIR/../scripts/install_verified_docker_apt_key.sh"
  # shellcheck disable=SC1091
  source /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME:?} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get "${APT_CONFOLD[@]}" -o Acquire::Retries=3 update -qq
  sudo apt-get "${APT_CONFOLD[@]}" -o Acquire::Retries=3 install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# Nested overlayfs cannot convert whiteout files; use fuse-overlayfs and the
# classic graphdriver instead of the containerd snapshotter. Never silently
# accept a pre-existing configuration that lacks either required setting.
if sudo test -L /etc/docker/daemon.json; then
  echo "Docker daemon configuration must not be a symlink." >&2
  exit 1
elif ! sudo test -e /etc/docker/daemon.json; then
  sudo mkdir -p /etc/docker
  echo '{ "features": { "containerd-snapshotter": false }, "storage-driver": "fuse-overlayfs" }' \
    | sudo tee /etc/docker/daemon.json >/dev/null
fi
if ! sudo /usr/bin/jq -e \
  '."storage-driver" == "fuse-overlayfs" and .features."containerd-snapshotter" == false' \
  /etc/docker/daemon.json >/dev/null; then
  echo "Docker daemon configuration lacks the required nested-engine settings." >&2
  exit 1
fi

echo "== [system] mise (pinned toolchain manager) =="
bash "$CURSOR_SETUP_DIR/../scripts/install_pinned_mise.sh"

echo "== [system] localias (local TLS reverse proxy) =="
bash "$CURSOR_SETUP_DIR/../scripts/install_pinned_localias.sh"

echo "== [system] done =="
