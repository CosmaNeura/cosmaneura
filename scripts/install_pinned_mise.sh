#!/usr/bin/env bash

set -euo pipefail

# Install the exact Mise artifact reviewed for the Cursor Cloud Agent bootstrap.
# The archive digest is published on the upstream GitHub release, and the
# binary digest matches both the archive member and the standalone release
# artifact. Never replace these pins with a mutable network-fetched installer.
MISE_VERSION="v2026.9.1"
MISE_ARCHIVE="mise-v2026.9.1-linux-x64.tar.gz"
MISE_ARCHIVE_SHA256="063dda9149ab6be53da877c2d176afe0eac68e64cf8ca295bd0528720701c65d"
MISE_BINARY_SHA256="c98423c8470d6dc416d9f7036d0646d8ef5ae92ad9186907f8fcc84cbe7db4ea"
MISE_URL="https://github.com/jdx/mise/releases/download/${MISE_VERSION}/${MISE_ARCHIVE}"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "Unsupported Mise platform: $(uname -s)/$(uname -m)" >&2
  exit 1
fi

if [[ -z "${HOME:-}" ]]; then
  echo "HOME is required to install Mise." >&2
  exit 1
fi

mise_install_dir="${HOME}/.local/bin"
mise_install_path="${mise_install_dir}/mise"

if [[ -x "${mise_install_path}" ]]; then
  installed_sha256="$(sha256sum "${mise_install_path}" | awk '{print $1}')"
  if [[ "${installed_sha256}" == "${MISE_BINARY_SHA256}" ]]; then
    echo "Mise ${MISE_VERSION} is already installed with the expected digest."
    exit 0
  fi
fi

mise_tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${mise_tmp_dir}"' EXIT
mise_download="${mise_tmp_dir}/${MISE_ARCHIVE}"

curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --retry 3 \
  --retry-all-errors \
  --retry-delay 2 \
  --output "${mise_download}" \
  "${MISE_URL}"

printf '%s  %s\n' "${MISE_ARCHIVE_SHA256}" "${mise_download}" | sha256sum --check --strict
tar --extract --gzip --file "${mise_download}" --directory "${mise_tmp_dir}" mise/bin/mise
printf '%s  %s\n' "${MISE_BINARY_SHA256}" "${mise_tmp_dir}/mise/bin/mise" | sha256sum --check --strict

mkdir -p "${mise_install_dir}"
install -m 0755 "${mise_tmp_dir}/mise/bin/mise" "${mise_install_path}"
printf '%s  %s\n' "${MISE_BINARY_SHA256}" "${mise_install_path}" | sha256sum --check --strict

echo "Installed Mise ${MISE_VERSION} with the expected digest."
