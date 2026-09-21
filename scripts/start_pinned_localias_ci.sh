#!/usr/bin/env bash

set -euo pipefail

LOCALIAS_SHA256="e7e8ae3a6cf2b1d7b637b6d871ea615b66509096f35ed8f4cc2c370a812193aa"
LOCALIAS_INSTALL_PATH="/usr/local/bin/localias"
LOCALIAS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Re-run the immutable installer as a verification step. It returns without a
# network request when the installed bytes match and otherwise installs only the
# same digest-pinned artifact.
bash "${LOCALIAS_SCRIPT_DIR}/install_pinned_localias.sh"
printf '%s  %s\n' "${LOCALIAS_SHA256}" "${LOCALIAS_INSTALL_PATH}" | sha256sum --check --strict

sudo -n -E "${LOCALIAS_INSTALL_PATH}" start
cert_location="$(sudo -n -E "${LOCALIAS_INSTALL_PATH}" debug cert)"
if [[ -z "${cert_location}" ]] || ! sudo test -f "${cert_location}"; then
  echo "Localias did not produce its expected root certificate." >&2
  exit 1
fi

nss_dir="${HOME}/.pki/nssdb"
mkdir -p "${nss_dir}"
if [[ ! -f "${nss_dir}/cert9.db" ]]; then
  certutil -d "sql:${nss_dir}" -N --empty-password </dev/null
fi
sudo certutil -d "sql:${nss_dir}" -D -n localias-cert </dev/null 2>/dev/null || true
sudo certutil -d "sql:${nss_dir}" -A -t "C,," -n localias-cert -i "${cert_location}" </dev/null
sudo update-ca-certificates --fresh </dev/null
sudo systemctl restart systemd-resolved

localias_config="$("${LOCALIAS_INSTALL_PATH}" debug config --print)"
localias_domains="$(
  awk -F: '!/^[[:space:]]*(#|$)/ {gsub(/[[:space:]]/, "", $1); print $1}' <<<"${localias_config}"
)"
if [[ -z "${localias_domains}" ]]; then
  echo "Localias returned no configured domains to verify." >&2
  exit 1
fi

while IFS= read -r localias_domain; do
  localias_url="https://${localias_domain}"
  curl_ready=false
  for _ in {1..5}; do
    if curl --silent --show-error --head "${localias_url}" >/dev/null; then
      curl_ready=true
      break
    fi
    sleep 2
  done
  if [[ "${curl_ready}" != "true" ]]; then
    echo "Localias did not serve ${localias_url}." >&2
    exit 1
  fi
  uv run python "${LOCALIAS_SCRIPT_DIR}/warm_localias_chromium.py" "${localias_url}"
done <<<"${localias_domains}"

printf '%s  %s\n' "${LOCALIAS_SHA256}" "${LOCALIAS_INSTALL_PATH}" | sha256sum --check --strict
