#!/usr/bin/env bash

set -euo pipefail

# Keep CI and the remote-development bootstrap on one immutable Localias
# artifact. The digest is the one published on the GitHub release asset and was
# independently verified against the downloaded linux-amd64 binary.
LOCALIAS_VERSION="v3.0.0+commit.43c3619"
LOCALIAS_ASSET="localias-linux-amd64"
LOCALIAS_SHA256="e7e8ae3a6cf2b1d7b637b6d871ea615b66509096f35ed8f4cc2c370a812193aa"
LOCALIAS_URL="https://github.com/peterldowns/localias/releases/download/v3.0.0%2Bcommit.43c3619/${LOCALIAS_ASSET}"
LOCALIAS_INSTALL_PATH="/usr/local/bin/localias"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "Unsupported Localias platform: $(uname -s)/$(uname -m)" >&2
  exit 1
fi

if [[ -x "${LOCALIAS_INSTALL_PATH}" ]]; then
  installed_sha256="$(sha256sum "${LOCALIAS_INSTALL_PATH}" | awk '{print $1}')"
  if [[ "${installed_sha256}" == "${LOCALIAS_SHA256}" ]]; then
    echo "Localias ${LOCALIAS_VERSION} is already installed with the expected digest."
    exit 0
  fi
fi

localias_tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${localias_tmp_dir}"' EXIT
localias_download="${localias_tmp_dir}/${LOCALIAS_ASSET}"

curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --retry 3 \
  --retry-all-errors \
  --retry-delay 2 \
  --output "${localias_download}" \
  "${LOCALIAS_URL}"

printf '%s  %s\n' "${LOCALIAS_SHA256}" "${localias_download}" | sha256sum --check --strict
sudo install -m 0755 "${localias_download}" "${LOCALIAS_INSTALL_PATH}"
printf '%s  %s\n' "${LOCALIAS_SHA256}" "${LOCALIAS_INSTALL_PATH}" | sha256sum --check --strict
