#!/usr/bin/env bash
# Re-exec Cursor entrypoints with a fixed, credential-neutral environment.
set -euo pipefail

CURSOR_CREDENTIAL_SCRIPT_DIR="$(cd "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd)"

cursor_require_credential_neutral() {
  if [[ "${COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL:-}" != "1" ]]; then
    echo "Cursor scripts must run through .cursor/credential-neutral.sh." >&2
    return 1
  fi

  local variable_name
  while IFS='=' read -r variable_name _; do
    case "${variable_name}" in
      *PASSWORD* | *SECRET* | *TOKEN* | *_KEY | *_KEY_* | DATABASE_URL | \
        TEST_DATABASE_URL | REDIS_URL | TEST_REDIS_URL | SMTP_URL | SSH_AUTH_SOCK)
        echo "Credential-bearing variable reached Cursor bootstrap: ${variable_name}." >&2
        return 1
        ;;
    esac
  done < <(/usr/bin/env)
}

cursor_exec_credential_neutral() {
  local cursor_user
  local cursor_runtime_home
  local cursor_state_path
  cursor_user="$(/usr/bin/id -un)"
  cursor_runtime_home="${CURSOR_CREDENTIAL_SCRIPT_DIR}/runtime-home"
  umask 077
  for cursor_state_path in \
    "${cursor_runtime_home}" \
    "${cursor_runtime_home}/.cache" \
    "${cursor_runtime_home}/.cache/uv" \
    "${cursor_runtime_home}/.config" \
    "${cursor_runtime_home}/.docker" \
    "${cursor_runtime_home}/.gnupg" \
    "${cursor_runtime_home}/.local" \
    "${cursor_runtime_home}/.local/share" \
    "${cursor_runtime_home}/.azure" \
    "${cursor_runtime_home}/.curl" \
    "${cursor_runtime_home}/tmp"; do
    if [[ -L "${cursor_state_path}" ]] \
      || { [[ -e "${cursor_state_path}" ]] && [[ ! -d "${cursor_state_path}" ]]; } \
      || { [[ -e "${cursor_state_path}" ]] && [[ ! -O "${cursor_state_path}" ]]; }; then
      echo "Cursor state path must be a real, user-owned directory: ${cursor_state_path}." >&2
      return 1
    fi
    /bin/mkdir -p "${cursor_state_path}"
    if [[ -L "${cursor_state_path}" || ! -d "${cursor_state_path}" || ! -O "${cursor_state_path}" ]]; then
      echo "Cursor state directory validation failed: ${cursor_state_path}." >&2
      return 1
    fi
    /bin/chmod 0700 "${cursor_state_path}"
  done
  exec /usr/bin/env -i \
    HOME="${cursor_runtime_home}" \
    USER="${cursor_user}" \
    LOGNAME="${cursor_user}" \
    SHELL=/bin/bash \
    PATH="${CURSOR_CREDENTIAL_SCRIPT_DIR}/credential-neutral-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    LANG=C \
    LC_ALL=C \
    TERM=xterm-256color \
    TMPDIR="${cursor_runtime_home}/tmp" \
    XDG_CACHE_HOME="${cursor_runtime_home}/.cache" \
    XDG_CONFIG_HOME="${cursor_runtime_home}/.config" \
    XDG_DATA_HOME="${cursor_runtime_home}/.local/share" \
    AZURE_CONFIG_DIR="${cursor_runtime_home}/.azure" \
    CURL_HOME="${cursor_runtime_home}/.curl" \
    DOCKER_CONFIG="${cursor_runtime_home}/.docker" \
    GIT_CONFIG_GLOBAL="${cursor_runtime_home}/.gitconfig" \
    GNUPGHOME="${cursor_runtime_home}/.gnupg" \
    NPM_CONFIG_USERCONFIG="${cursor_runtime_home}/.npmrc" \
    PIP_CONFIG_FILE="${cursor_runtime_home}/pip.conf" \
    UV_CACHE_DIR="${cursor_runtime_home}/.cache/uv" \
    COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL=1 \
    "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ "$#" != 1 ]]; then
    echo "Usage: $0 {install|start|backend|frontend}" >&2
    exit 2
  fi

  case "$1" in
    install | start)
      cursor_target="${CURSOR_CREDENTIAL_SCRIPT_DIR}/$1.sh"
      ;;
    backend | frontend)
      cursor_target="${CURSOR_CREDENTIAL_SCRIPT_DIR}/run-$1.sh"
      ;;
    *)
      echo "Unknown Cursor environment entrypoint: $1" >&2
      exit 2
      ;;
  esac

  cursor_exec_credential_neutral /bin/bash "${cursor_target}"
fi
