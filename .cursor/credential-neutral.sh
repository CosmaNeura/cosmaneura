#!/usr/bin/env bash
# Re-exec Cursor entrypoints with a fixed, credential-neutral environment.
set -euo pipefail

CURSOR_CREDENTIAL_SCRIPT_DIR="$(cd "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CURSOR_MISE_BINARY_SHA256="c98423c8470d6dc416d9f7036d0646d8ef5ae92ad9186907f8fcc84cbe7db4ea"

cursor_require_exact_mise_binary() {
  local mise_binary="$1"
  local mise_digest

  if [[ -L "${mise_binary}" || ! -f "${mise_binary}" || ! -O "${mise_binary}" || ! -x "${mise_binary}" ]]; then
    echo "Cursor Mise must be a real, user-owned executable: ${mise_binary}." >&2
    return 1
  fi
  if [[ -x /usr/bin/sha256sum ]]; then
    mise_digest="$(/usr/bin/sha256sum -- "${mise_binary}")" || return 1
  elif [[ -x /usr/bin/shasum ]]; then
    mise_digest="$(/usr/bin/shasum -a 256 -- "${mise_binary}")" || return 1
  else
    echo "Unable to hash the Cursor Mise executable." >&2
    return 1
  fi
  mise_digest="${mise_digest%% *}"
  if [[ "${mise_digest}" != "${CURSOR_MISE_BINARY_SHA256}" ]]; then
    echo "Cursor Mise executable does not match the reviewed digest." >&2
    return 1
  fi
}

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
  local cursor_direnv_config
  local cursor_forbidden_hook
  local cursor_mise_binary
  local cursor_mise_config
  local cursor_mise_system_data
  local cursor_mise_system_config
  local cursor_repo_parent
  local cursor_repo_root
  local cursor_unexpected_executable
  local cursor_user
  local cursor_runtime_home
  local cursor_state_file
  local cursor_state_path
  cursor_user="$(/usr/bin/id -un)"
  cursor_repo_root="$(cd "${CURSOR_CREDENTIAL_SCRIPT_DIR}/.." && pwd)"
  cursor_repo_parent="$(/usr/bin/dirname -- "${cursor_repo_root}")"
  cursor_runtime_home="${CURSOR_CREDENTIAL_SCRIPT_DIR}/runtime-home"
  cursor_direnv_config="${cursor_runtime_home}/direnv-config"
  cursor_mise_binary="${cursor_runtime_home}/.local/bin/mise"
  cursor_mise_config="${cursor_runtime_home}/mise-config"
  cursor_mise_system_data="${cursor_runtime_home}/mise-system-data"
  cursor_mise_system_config="${cursor_runtime_home}/mise-system-config"
  umask 077
  for cursor_state_path in \
    "${cursor_runtime_home}" \
    "${cursor_runtime_home}/.cache" \
    "${cursor_runtime_home}/.cache/ms-playwright" \
    "${cursor_runtime_home}/.cache/uv" \
    "${cursor_runtime_home}/.config" \
    "${cursor_runtime_home}/.docker" \
    "${cursor_runtime_home}/.gnupg" \
    "${cursor_runtime_home}/.local" \
    "${cursor_runtime_home}/.local/bin" \
    "${cursor_runtime_home}/.local/share" \
    "${cursor_runtime_home}/.local/share/mise" \
    "${cursor_runtime_home}/.local/share/mise/shims" \
    "${cursor_runtime_home}/.local/state" \
    "${cursor_runtime_home}/.local/state/localias" \
    "${cursor_runtime_home}/.pki" \
    "${cursor_runtime_home}/.pki/nssdb" \
    "${cursor_runtime_home}/.azure" \
    "${cursor_runtime_home}/.curl" \
    "${cursor_direnv_config}" \
    "${cursor_mise_config}" \
    "${cursor_mise_system_data}" \
    "${cursor_mise_system_config}" \
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
  for cursor_forbidden_hook in \
    "${cursor_runtime_home}/.direnvrc" \
    "${cursor_runtime_home}/.config/direnv"; do
    if [[ -e "${cursor_forbidden_hook}" || -L "${cursor_forbidden_hook}" ]]; then
      echo "Persisted Direnv hooks are forbidden in the Cursor runtime home: ${cursor_forbidden_hook}." >&2
      return 1
    fi
  done
  if [[ -e "${cursor_runtime_home}/.config/mise" || -L "${cursor_runtime_home}/.config/mise" ]]; then
    echo "Persisted global Mise configuration is forbidden in the Cursor runtime home." >&2
    return 1
  fi
  for cursor_state_path in \
    "${cursor_direnv_config}" \
    "${cursor_mise_config}" \
    "${cursor_mise_system_config}" \
    "${cursor_mise_system_data}"; do
    if [[ -n "$(/usr/bin/find "${cursor_state_path}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      echo "Dedicated Cursor state directory must remain empty: ${cursor_state_path}." >&2
      return 1
    fi
    /bin/chmod 0500 "${cursor_state_path}"
  done
  for cursor_state_file in \
    "${cursor_runtime_home}/.gitconfig" \
    "${cursor_runtime_home}/.npmrc" \
    "${cursor_runtime_home}/pip.conf" \
    "${cursor_runtime_home}/.docker/config.json" \
    "${cursor_runtime_home}/.curl/.curlrc" \
    "${cursor_runtime_home}/.pki/nssdb/cert9.db" \
    "${cursor_runtime_home}/.pki/nssdb/key4.db" \
    "${cursor_runtime_home}/.pki/nssdb/pkcs11.txt"; do
    if [[ -L "${cursor_state_file}" ]] \
      || { [[ -e "${cursor_state_file}" ]] && [[ ! -f "${cursor_state_file}" ]]; } \
      || { [[ -e "${cursor_state_file}" ]] && [[ ! -O "${cursor_state_file}" ]]; }; then
      echo "Cursor config path must be a real, user-owned regular file: ${cursor_state_file}." >&2
      return 1
    fi
    if [[ -e "${cursor_state_file}" ]]; then
      /bin/chmod 0600 "${cursor_state_file}"
    fi
  done
  cursor_unexpected_executable="$(
    /usr/bin/find "${cursor_runtime_home}/.local/bin" \
      -mindepth 1 -maxdepth 1 ! -name mise -print -quit
  )"
  if [[ -n "${cursor_unexpected_executable}" ]]; then
    echo "Unexpected executable entry in the Cursor local bin directory." >&2
    return 1
  fi
  if [[ -e "${cursor_mise_binary}" || -L "${cursor_mise_binary}" ]]; then
    cursor_require_exact_mise_binary "${cursor_mise_binary}"
  fi
  cd "${cursor_repo_root}"
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
    XDG_STATE_HOME="${cursor_runtime_home}/.local/state" \
    DIRENV_CONFIG="${cursor_direnv_config}" \
    MISE_CONFIG_DIR="${cursor_mise_config}" \
    MISE_SYSTEM_CONFIG_DIR="${cursor_mise_system_config}" \
    MISE_GLOBAL_CONFIG_FILE="${cursor_mise_config}/global-config.toml" \
    MISE_CEILING_PATHS="${cursor_repo_parent}" \
    MISE_NO_HOOKS=1 \
    MISE_SYSTEM_DATA_DIR="${cursor_mise_system_data}" \
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

  cursor_exec_credential_neutral /bin/bash --noprofile --norc -- "${cursor_target}"
fi
