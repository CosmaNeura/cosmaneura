#!/usr/bin/env bash
set -euo pipefail
# This root-owned hook executes before checkout or any workflow step. Its path
# comes from the runner process environment, not workflow-controlled job inputs.
target_file=/opt/local-release-target-sha
if [[ ! -f "$target_file" || -L "$target_file" || \
      "$(stat -c %u "$target_file")" != 0 || "$(stat -c %a "$target_file")" != 444 ]]; then
  echo 'Local release target is not protected.' >&2
  exit 1
fi
target_sha=$(cat "$target_file")
case "${GITHUB_WORKFLOW_REF:-}" in
  CosmaNeura/cosmaneura/.github/workflows/infra_production_plan_local.yml@refs/heads/master|\
  CosmaNeura/cosmaneura/.github/workflows/infra_apply_local.yml@refs/heads/master|\
  CosmaNeura/cosmaneura/.github/workflows/build_and_publish_local.yml@refs/heads/master) ;;
  *) echo 'This workflow is not admitted to the local release runner.' >&2; exit 1 ;;
esac
if [[ "${GITHUB_EVENT_NAME:-}" != workflow_dispatch || \
      "${GITHUB_REF:-}" != refs/heads/master || \
      "${GITHUB_SHA:-}" != "$target_sha" || \
      "${GITHUB_ACTOR:-}" != JohnClem || \
      "${GITHUB_TRIGGERING_ACTOR:-}" != JohnClem ]]; then
  echo 'Local runner refuses a non-founder, unmerged or stale release job.' >&2
  exit 1
fi
