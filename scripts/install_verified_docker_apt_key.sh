#!/usr/bin/env bash

set -euo pipefail

# Docker documents this signing key at the exact distribution endpoint below.
# Verify the sole primary key fingerprint before and after dearmoring so the
# mutable URL cannot silently replace the repository-approved trust root.
DOCKER_APT_KEY_URL="https://download.docker.com/linux/ubuntu/gpg"
DOCKER_APT_KEY_PRIMARY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
DOCKER_APT_KEY_PATH="/etc/apt/keyrings/docker.gpg"

docker_key_tmp_dir="$(mktemp -d)"
docker_key_target_tmp=""

cleanup_docker_key() {
  rm -rf -- "${docker_key_tmp_dir}"
  if [[ -n "${docker_key_target_tmp}" ]]; then
    sudo rm -f -- "${docker_key_target_tmp}" || true
  fi
}
trap cleanup_docker_key EXIT

docker_gnupg_home="${docker_key_tmp_dir}/gnupg"
mkdir -m 0700 "${docker_gnupg_home}"

docker_primary_fingerprints() {
  gpg \
    --homedir "${docker_gnupg_home}" \
    --no-options \
    --batch \
    --show-keys \
    --with-colons \
    "$1" 2>/dev/null \
    | awk -F: '
        $1 == "pub" { awaiting_primary_fingerprint = 1; next }
        awaiting_primary_fingerprint && $1 == "fpr" {
          print toupper($10)
          awaiting_primary_fingerprint = 0
        }
      '
}

require_exact_docker_primary_fingerprint() {
  local key_path="$1"
  local fingerprints
  fingerprints="$(docker_primary_fingerprints "${key_path}")"
  if [[ "${fingerprints}" != "${DOCKER_APT_KEY_PRIMARY_FINGERPRINT}" ]]; then
    echo "Docker apt signing key did not have the exact approved sole primary fingerprint." >&2
    return 1
  fi
}

if [[ -r "${DOCKER_APT_KEY_PATH}" ]] \
  && require_exact_docker_primary_fingerprint "${DOCKER_APT_KEY_PATH}"; then
  echo "Docker apt signing key already has the exact approved fingerprint."
  exit 0
fi

docker_key_download="${docker_key_tmp_dir}/docker.asc"
docker_key_dearmored="${docker_key_tmp_dir}/docker.gpg"

curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --retry 3 \
  --retry-all-errors \
  --retry-delay 2 \
  --output "${docker_key_download}" \
  "${DOCKER_APT_KEY_URL}"

require_exact_docker_primary_fingerprint "${docker_key_download}"
gpg \
  --homedir "${docker_gnupg_home}" \
  --no-options \
  --batch \
  --yes \
  --dearmor \
  --output "${docker_key_dearmored}" \
  "${docker_key_download}"
require_exact_docker_primary_fingerprint "${docker_key_dearmored}"

sudo install -m 0755 -d "$(dirname "${DOCKER_APT_KEY_PATH}")"
docker_key_target_tmp="$(sudo mktemp "${DOCKER_APT_KEY_PATH}.tmp.XXXXXX")"
sudo install -m 0644 "${docker_key_dearmored}" "${docker_key_target_tmp}"
sudo mv -f -- "${docker_key_target_tmp}" "${DOCKER_APT_KEY_PATH}"
docker_key_target_tmp=""

require_exact_docker_primary_fingerprint "${DOCKER_APT_KEY_PATH}"
echo "Installed Docker apt signing key with the exact approved fingerprint."
