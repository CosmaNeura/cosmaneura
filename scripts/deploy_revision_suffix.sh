#!/usr/bin/env bash

# Produce one Azure Container Apps revision suffix per GitHub Actions attempt.
# A rerun receives a new attempt number, so it can safely finish a deployment
# that stopped after only some workloads were updated. Controlled non-Actions
# invocations must provide the equivalent DEPLOY_RUN_* values explicitly.
generate_deploy_revision_suffix() {
	local git_sha="$1"
	local run_id="${DEPLOY_RUN_ID:-${GITHUB_RUN_ID:-}}"
	local run_attempt="${DEPLOY_RUN_ATTEMPT:-${GITHUB_RUN_ATTEMPT:-}}"

	if [[ ! "$run_id" =~ ^[1-9][0-9]*$ ]]; then
		echo "Deployment run ID must be a positive integer." >&2
		return 1
	fi
	if [[ ! "$run_attempt" =~ ^[1-9][0-9]*$ ]]; then
		echo "Deployment run attempt must be a positive integer." >&2
		return 1
	fi
	if [[ ! "$git_sha" =~ ^[0-9a-f]{40}$ ]]; then
		echo "Deployment Git SHA must be a full lowercase commit SHA." >&2
		return 1
	fi

	printf 'r%sa%s-%s\n' "$run_id" "$run_attempt" "${git_sha:0:7}"
}

validate_deploy_revision_suffix() {
	local suffix="$1"
	shift

	if [[ ! "$suffix" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
		echo "Generated deployment revision suffix is invalid." >&2
		return 1
	fi
	if [[ "$suffix" == *--* ]]; then
		echo "Generated deployment revision suffix contains consecutive hyphens." >&2
		return 1
	fi
	if [[ $# -eq 0 ]]; then
		echo "At least one Container App name is required for revision validation." >&2
		return 1
	fi

	local container_name revision_name
	for container_name in "$@"; do
		revision_name="${container_name}--${suffix}"
		if (( ${#revision_name} > 54 )); then
			echo "Deployment revision name would exceed Azure's 54-character limit for ${container_name}." >&2
			return 1
		fi
	done
}
