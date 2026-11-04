from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLAG = "HELD_MEDICATION_WRITES_ENABLED"
INPUT = FLAG + "_INPUT"
ExportProduction = tuple[
    Callable[[dict[str, str]], subprocess.CompletedProcess[str]], Path
]


@pytest.fixture
def export_production(tmp_path: Path) -> ExportProduction:
    """Run the normal just recipe with data-only vault/direnv boundary doubles.

    This follows existing backend-template tests. No real op/direnv, trust,
    application import, database or cloud client is invoked.
    """
    just = shutil.which("just")
    jq = shutil.which("jq")
    assert just is not None and jq is not None
    assert (
        'set shell := ["zsh", "-cu", "-o", "pipefail"]'
        in (ROOT / "Justfile").read_text()
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # Execute the existing tool without copying platform code-signing metadata.
    (binaries / "jq").symlink_to(jq)
    driver = binaries / "direnv"
    vault_trace = tmp_path / "synthetic-vault-calls"
    driver.write_text(
        "#!/bin/bash\nset -eu\n"
        + r"""
[[ "$*" == "export json" ]]
[[ ! ${HELD_MEDICATION_WRITES_ENABLED+present} ]]
[[ ! ${UNRELATED_AMBIENT_SETTING+present} ]]
op_inject_source() {
 """
        + f"printf 'called\\n' >> {str(vault_trace)!r}\n"
        + r"""
 while IFS= read -r line; do
  if [[ "$line" =~ ^export\ ([A-Z][A-Z0-9_]*)= ]]; then
   variable=${BASH_REMATCH[1]}
   export "$variable=fixture-$variable"
  fi
 done
}
python() {
 case "$1" in
  scripts/superadmin_capability_config_preflight.py) printf '%s' 'https://fictional.clerk.invalid';;
  scripts/core_cab_runtime_config.py) return 0;;
  *) return 17;;
 esac
}
source "$RENDER_DIRENV"
"""
        + f"\n{sys.executable!r} -c 'import json,os;print(json.dumps(dict(os.environ)))'\n"
    )
    driver.chmod(0o700)

    def run(overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                just,
                "--shell=zsh",
                "--clear-shell-args",
                "--shell-arg=-cu",
                "--shell-arg=-o",
                "--shell-arg=pipefail",
                "--justfile",
                str(ROOT / "just/direnv.just"),
                "direnv_export",
                str(ROOT / ".env.production.backend"),
            ],
            cwd=ROOT,
            env={
                "PATH": str(binaries) + os.pathsep + "/usr/bin:/bin",
                "HOME": os.environ["HOME"],
                "LANG": "C",
                "UNRELATED_AMBIENT_SETTING": "must-not-pass",
                **overrides,
            },
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    return run, vault_trace


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "false"), ("", "false"), ("false", "false"), ("true", "true")],
)
def test_normal_export_retains_explicit_held_value_and_existing_settings(
    export_production: ExportProduction, value: str | None, expected: str
) -> None:
    run, vault_trace = export_production
    result = run({} if value is None else {FLAG: value})
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert vault_trace.is_file()
    values = json.loads(result.stdout)
    assert values[FLAG] == expected
    assert INPUT not in values
    assert "UNRELATED_AMBIENT_SETTING" not in values
    assert values["PYTHON_ENV"] == "production"
    assert values["DATABASE_URL_SECRET_NAME"] == "database-url"
    assert values["DATABASE_URL_V2_ENABLED"] == "false"
    assert values["DATABASE_URL"] == "fixture-DATABASE_URL"
    assert values["SCRIBE_HANDOFF_ENABLED"] == "false"
    assert values["STRIPE_INTEGRATION_ENABLED"] == "false"
    assert values["STRIPE_BILLING_ENABLED"] == "false"
    assert values["AUTHORITY_KERNEL_ENABLED"] == "false"
    assert values["AGENT_API_ENABLED"] == "false"


@pytest.mark.parametrize("value", ["TRUE", "yes", "1", "false ", " true", "true;bad"])
def test_invalid_held_input_refuses_before_vault_resolution(
    export_production: ExportProduction, value: str
) -> None:
    run, vault_trace = export_production
    result = run({FLAG: value})
    assert result.returncode != 0
    assert result.stdout == ""
    assert (
        "HELD_MEDICATION_WRITES_ENABLED must be literal true or false." in result.stderr
    )
    assert "fixture-" not in result.stderr
    assert not vault_trace.exists()


def test_unforwarded_helper_input_cannot_enable_holds(
    export_production: ExportProduction,
) -> None:
    run, _vault_trace = export_production
    result = run({INPUT: "true"})
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert values[FLAG] == "false"
    assert INPUT not in values


def test_production_public_key_uses_existing_all_role_projection() -> None:
    variables = (ROOT / "infra/variables.tf").read_text()
    public = variables.split('variable "python_public_vars" {', 1)[1]
    default = public.split("validation {", 1)[0]
    assert default.count(f'"{FLAG}"') == 1
    main = (ROOT / "infra/main.tf").read_text()
    for role in ("app", "worker", "scheduler"):
        block = main.split(f'resource "azurerm_container_app" "{role}" {{', 1)[1]
        block = block.split('\nresource "', 1)[0]
        assert "for_each = var.python_public_vars" in block or (
            "for_each = setunion(var.python_public_vars, var.python_billing_public_vars)"
            in block
        )
    # It neither duplicates the independently controlled startup/global keys
    # nor places the held control in the owned migration job's public set.
    shared = (ROOT / "infra/session_prep_rollout.tf").read_text()
    assert FLAG not in shared
    assert 'AUTO_MIGRATE_ON_STARTUP       = "false"' in shared
    deploy = (ROOT / "infra/deploy.sh").read_text()
    migration = deploy.split("MIGRATION_PUBLIC_VARS=(", 1)[1].split("\n)", 1)[0]
    assert FLAG not in migration
