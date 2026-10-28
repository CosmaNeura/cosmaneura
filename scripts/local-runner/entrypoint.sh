#!/usr/bin/env bash
set -euo pipefail
# The target is immutable before any job is admitted. Only the trusted entrypoint
# writes it; workflow environment values cannot replace this root-owned file.
IFS= read -r target_sha
[[ "$target_sha" =~ ^[a-f0-9]{40}$ ]]
IFS= read -r target_run
IFS= read -r target_attempt
[[ "$target_run" =~ ^[1-9][0-9]*$ && "$target_attempt" =~ ^[1-9][0-9]*$ ]]
[[ "${LOCAL_RUNNER_CONTAINER:-}" =~ ^cosma-local-runner-[a-f0-9]{16}$ ]]
printf '%s\n' "$target_sha" "$target_run" "$target_attempt" | sudo tee /opt/local-release-target-sha >/dev/null
printf '%s\n' "$LOCAL_RUNNER_CONTAINER" | sudo tee /opt/local-release-runner-name >/dev/null
sudo chmod 0444 /opt/local-release-target-sha
sudo chmod 0444 /opt/local-release-runner-name
unset target_sha target_run target_attempt
# JIT credentials arrive over stdin, never through command arguments or logs.
IFS= read -r jit_config
export ACTIONS_RUNNER_INPUT_JITCONFIG="$jit_config"
unset jit_config
# Runner.Listener consumes and clears this documented input environment variable.
exec ./run.sh
