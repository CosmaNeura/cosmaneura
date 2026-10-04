"""Deployment ownership for the dormant Zoom watchdog rollout gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FLAG = "ZOOM_WATCHDOG_ENABLED"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _hcl_block(source: str, header: str) -> str:
    start = source.index(header)
    opening = source.index("{", start)
    depth = 0
    for offset, character in enumerate(source[opening:], start=opening):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start : offset + 1]
    raise AssertionError(f"unterminated HCL block: {header}")


def _shell_array(source: str, name: str) -> str:
    start = source.index(f"\n{name}=(") + 1
    end = source.index("\n)", start)
    return source[start : end + 2]


def _assert_watchdog_disabled_in_secret_overrides(source: str) -> None:
    overrides = [
        _hcl_block(source[match.start() :], "override_data")
        for match in re.finditer(r"^override_data\s*\{", source, re.MULTILINE)
    ]
    secret_overrides = [
        block
        for block in overrides
        if re.search(
            r"^\s*target\s*=\s*data\.external\.secrets\s*$", block, re.MULTILINE
        )
    ]
    assert secret_overrides, "secretless plan must override external secrets"
    for block in secret_overrides:
        declarations = re.findall(rf"^\s*{FLAG}\s*=\s*(.*)$", block, re.MULTILINE)
        assert declarations == ['"false"'], (
            "each secrets scenario must disable watchdog"
        )


@pytest.mark.parametrize(
    "unsafe_value", ['"true"', "", '"false"\nZOOM_WATCHDOG_ENABLED = "false"']
)
def test_watchdog_scenario_guard_rejects_enabled_missing_or_duplicate_flags(
    unsafe_value: str,
) -> None:
    def scenario(value: str) -> str:
        declaration = f"{FLAG} = {value}" if value else ""
        return f"override_data {{\ntarget = data.external.secrets\nvalues = {{\n{declaration}\n}}\n}}\n"

    safe = scenario('"false"')
    _assert_watchdog_disabled_in_secret_overrides(safe + safe)
    with pytest.raises(AssertionError):
        _assert_watchdog_disabled_in_secret_overrides(safe + scenario(unsafe_value))


def _workflow_job(source: str, name: str) -> str:
    start = source.index(f"  {name}:\n")
    match = re.search(r"\n  [a-z][a-z0-9_-]*:\n", source[start + 1 :])
    if match is None:
        return source[start:]
    return source[start : start + 1 + match.start()]


def _workflow_step(job: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = job.index(marker)
    end = job.find("\n      - ", start + len(marker))
    return job[start:] if end == -1 else job[start:end]


def test_backend_environments_source_lock_watchdog_false() -> None:
    declaration = f"export {FLAG}=false"
    for path in (".env.staging.backend", ".env.production.backend"):
        source = _read(path)
        assert source.count(declaration) == 1
        assert f'export {FLAG}="${{' not in source


def test_watchdog_is_public_in_both_terraform_environments() -> None:
    for directory in ("infra-staging", "infra"):
        variables = _read(f"{directory}/variables.tf")
        public = _hcl_block(variables, 'variable "python_public_vars"')
        common_secret = _hcl_block(variables, 'variable "python_secret_vars"')
        api_secret = _hcl_block(variables, 'variable "python_api_only_secret_vars"')
        assert public.count(FLAG) == 1
        assert FLAG not in common_secret
        assert FLAG not in api_secret

    production = _read("infra/main.tf")
    for workload in ("app", "worker", "scheduler"):
        block = _hcl_block(production, f'resource "azurerm_container_app" "{workload}"')
        assert "var.python_public_vars" in block

    secretless_plan = _read("infra/tests/secretless_plan.tftest.hcl")
    _assert_watchdog_disabled_in_secret_overrides(secretless_plan)


def test_staging_deploy_forces_evidences_and_reads_back_watchdog_false() -> None:
    deploy = _read("infra-staging/deploy.sh")
    reconciliation = deploy[
        deploy.index(
            "# Keep the provider-v7 runtime gates synchronized"
        ) : deploy.index("\nemit_github_output() {")
    ]
    evidence = deploy[
        deploy.index("build_configuration_evidence() {") : deploy.index(
            "\nif ! CONFIGURATION_EVIDENCE_JSON="
        )
    ]
    public = _shell_array(deploy, "PUBLIC_VARS")
    readback = deploy[
        deploy.index("collect_workload_state() {") : deploy.index(
            "\nverify_workload_state_ready() {"
        )
    ]
    verification = deploy[
        deploy.index("verify_candidate_workload_state() {") : deploy.index(
            "\ncapture_failed_workload_state() {"
        )
    ]

    assert f'{FLAG}: "false"' in reconciliation
    assert f" {FLAG} " in evidence
    assert FLAG in public
    assert "publicConfig: $public_config" in readback
    assert "$expected.flags" in verification
    assert ".publicConfig" in verification


def test_staging_workflow_requires_and_records_disabled_watchdog() -> None:
    workflow = _read(".github/workflows/deploy_staging_candidate.yml")
    publish = _workflow_job(workflow, "publish")
    deploy = _workflow_job(workflow, "deploy")
    source_locked = f'{FLAG}: "false"'

    publication = _workflow_step(publish, "Validate staging publication controls")
    identity = _workflow_step(
        deploy, "Validate exact staging identity inputs and release controls"
    )
    execution = _workflow_step(
        deploy, "Deploy candidate digest with protected-master controls"
    )
    evidence = _workflow_step(deploy, "Assemble non-secret staging release evidence")

    for step in (publication, identity, execution, evidence):
        assert source_locked in step
    assert f'"${FLAG}" != "false"' in publication
    assert f'"${FLAG}" != "false"' in identity
    assert f".flags.{FLAG} // empty" in evidence
    assert '"$observed_zoom_watchdog_enabled" == "false"' in evidence
    assert f"{FLAG}: $zoom_watchdog_enabled" in evidence

    attempt = _workflow_job(workflow, "attempt_evidence")
    terminal = _workflow_step(attempt, "Require a complete successful staging attempt")
    assert f'.flags.{FLAG} == "false"' in terminal
    assert f'.publicConfig.{FLAG} == "false"' in terminal
