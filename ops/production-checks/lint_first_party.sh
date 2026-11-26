#!/usr/bin/env bash
# Preserve repository lint settings and verify the three unmodified vendor files
# by provenance. Run from a clean Actions checkout.
set -euo pipefail

python3 -B -c 'import sys; sys.path.insert(0, "ops/production-checks"); from contract import verify_package; verify_package()'

(
  set -o noclobber
  cat > pyrightconfig.json <<'JSON'
{
  "extends": "./pyproject.toml",
  "ignore": [
    "ops/production-checks/runner.py",
    "ops/production-checks/tests/pass_check.py",
    "ops/production-checks/tests/test_runner.py"
  ]
}
JSON
)
trap 'rm -f pyrightconfig.json' EXIT
just py_lint
