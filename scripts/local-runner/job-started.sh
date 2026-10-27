#!/usr/bin/env bash
set -euo pipefail
# This root-owned hook executes before checkout or any workflow step. Its path
# comes from the runner process environment, not workflow-controlled job inputs.
protected_file() {
  [[ -f "$1" && ! -L "$1" && "$(stat -c %u "$1")" == 0 && "$(stat -c %a "$1")" == 444 ]]
}
refuse() {
  echo 'Local runner refuses an unapproved release job; terminating the disposable runner.' >&2
  # Failure alone is insufficient: GitHub would still execute an always() step.
  # Kill this exact container through the daemon before any subsequent step.
  if protected_file /opt/local-release-runner-name; then
    name=$(cat /opt/local-release-runner-name)
    if [[ "$name" =~ ^cosma-local-runner-[a-f0-9]{16}$ ]] && \
       [[ "$(docker inspect --format '{{index .Config.Labels "cosmaneura.local-release"}}' "$name" 2>/dev/null || true)" == true ]] && \
       [[ "$(docker inspect --format '{{.Config.Hostname}}' "$name" 2>/dev/null || true)" == "$(cat /etc/hostname)" ]]; then
      docker kill --signal KILL "$name" >/dev/null 2>&1 || true
    fi
  fi
  # The driver uses Docker's private PID namespace. If its API is unavailable,
  # kill the listener/worker processes in that namespace; no always() evaluator
  # can survive. The run.sh PID1 wrapper then exits after its listener dies.
  if [[ -f /.dockerenv && "$(id -u)" == 1001 ]]; then
    kill -KILL -1 || true
  fi
  exit 1
}
target_file=/opt/local-release-target-sha
protected_file "$target_file" || refuse
protected_file /opt/local-release-runner-name || refuse
target_sha=$(sed -n '1p' "$target_file")
target_run=$(sed -n '2p' "$target_file")
target_attempt=$(sed -n '3p' "$target_file")
[[ "$target_sha" =~ ^[a-f0-9]{40}$ && "$target_run" =~ ^[1-9][0-9]*$ && "$target_attempt" =~ ^[1-9][0-9]*$ ]] || refuse
case "${GITHUB_WORKFLOW_REF:-}" in
  CosmaNeura/cosmaneura/.github/workflows/infra_production_plan_local.yml@refs/heads/master|\
  CosmaNeura/cosmaneura/.github/workflows/infra_apply_local.yml@refs/heads/master|\
  CosmaNeura/cosmaneura/.github/workflows/build_and_publish_local.yml@refs/heads/master) ;;
  *) refuse ;;
esac
if [[ "${GITHUB_EVENT_NAME:-}" != workflow_dispatch || \
      "${GITHUB_REF:-}" != refs/heads/master || \
      "${GITHUB_SHA:-}" != "$target_sha" || \
      "${GITHUB_RUN_ID:-}" != "$target_run" || \
      "${GITHUB_RUN_ATTEMPT:-}" != "$target_attempt" || \
      "${GITHUB_ACTOR:-}" != JohnClem || \
      "${GITHUB_TRIGGERING_ACTOR:-}" != JohnClem ]]; then
  refuse
fi
