#!/usr/bin/env bash
set -euo pipefail
# The target is immutable before any job is admitted. Only the trusted entrypoint
# writes it; workflow environment values cannot replace this root-owned file.
IFS= read -r target_sha
[[ "$target_sha" =~ ^[a-f0-9]{40}$ ]]
printf '%s\n' "$target_sha" | sudo tee /opt/local-release-target-sha >/dev/null
sudo chmod 0444 /opt/local-release-target-sha
unset target_sha
# JIT credentials arrive over stdin, never through command arguments or logs.
IFS= read -r jit_config
export ACTIONS_RUNNER_INPUT_JITCONFIG="$jit_config"
unset jit_config
# Runner.Listener consumes and clears this documented input environment variable.
exec ./run.sh
