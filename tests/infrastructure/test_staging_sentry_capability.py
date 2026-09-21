from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = "SENTRY_STAGING_ENABLED"


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
    match = re.search(
        rf"^{name}=\(\n(?P<body>.*?)^\)$", source, re.MULTILINE | re.DOTALL
    )
    assert match is not None
    return match.group("body")


def _shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}", start)
    return source[start : end + 2]


def _workflow_job(source: str, name: str) -> str:
    start = source.index(f"\n  {name}:\n")
    match = re.search(r"\n  [a-z][a-z0-9_-]*:\n", source[start + 1 :])
    if match is None:
        return source[start:]
    return source[start : start + 1 + match.start()]


def _workflow_step(job: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = job.index(marker)
    end = job.find("\n      - ", start + len(marker))
    return job[start:] if end == -1 else job[start:end]


def test_staging_sentry_control_is_default_off_and_staging_only() -> None:
    staging_environment = _read(".env.staging.backend")
    production_environment = _read(".env.production.backend")
    direnv = _read("just/direnv.just")
    legacy_workflow = _read(".github/workflows/build_and_publish_manual.yml")
    legacy_production = legacy_workflow[legacy_workflow.index("\n  deploy:\n") :]
    # Staging's source-locked Sentry control moved to the canonical candidate
    # workflow when the staging-branch deploy path was retired.
    canonical_workflow = _read(".github/workflows/deploy_staging_candidate.yml")

    assert staging_environment.count(f"export {CONTROL}=false") == 1
    assert f'export {CONTROL}="${{' not in staging_environment
    assert CONTROL not in direnv
    assert f'{CONTROL}: "false"' in canonical_workflow
    assert f'"${CONTROL}" != "false"' in canonical_workflow
    assert f"vars.{CONTROL}" not in legacy_workflow
    assert f"vars.{CONTROL}" not in canonical_workflow
    assert CONTROL not in production_environment
    assert CONTROL not in legacy_production


def test_staging_terraform_and_deployer_keep_flag_public_and_dsn_secret() -> None:
    staging_variables = _read("infra-staging/variables.tf")
    terraform_public = _hcl_block(staging_variables, 'variable "python_public_vars"')
    terraform_common_secret = _hcl_block(
        staging_variables, 'variable "python_secret_vars"'
    )
    terraform_api_secret = _hcl_block(
        staging_variables, 'variable "python_api_only_secret_vars"'
    )
    deploy = _read("infra-staging/deploy.sh")
    public_vars = _shell_array(deploy, "PUBLIC_VARS")
    secret_vars = _shell_array(deploy, "COMMON_SECRET_VARS")
    configuration_evidence = _shell_function(deploy, "build_configuration_evidence")

    assert terraform_public.count(CONTROL) == 1
    assert CONTROL not in terraform_common_secret
    assert CONTROL not in terraform_api_secret
    assert f'"{CONTROL}"' not in _read("infra/variables.tf")
    assert CONTROL in public_vars
    assert CONTROL not in secret_vars
    assert "SENTRY_DSN" in secret_vars
    assert CONTROL in configuration_evidence
    assert "SENTRY_DSN" not in configuration_evidence
    assert f'{CONTROL}: "false"' in deploy
    forced_false = next(
        line
        for line in configuration_evidence.splitlines()
        if line.startswith("forced_false =")
    )
    assert CONTROL in forced_false
    assert f"${{{CONTROL}:-" not in deploy
    assert "($expected.urls + $expected.flags" in deploy
    assert ".publicConfig[$item.key] == $item.value" in deploy


def test_canonical_workflow_validates_and_records_managed_flag() -> None:
    workflow = _read(".github/workflows/deploy_staging_candidate.yml")
    setting = f'{CONTROL}: "false"'
    publish = _workflow_job(workflow, "publish")
    deploy = _workflow_job(workflow, "deploy")

    assert setting in publish
    assert deploy.count(setting) == 3
    assert f"vars.{CONTROL}" not in workflow
    publication = _workflow_step(publish, "Validate staging publication controls")
    identity = _workflow_step(
        deploy, "Validate exact staging identity inputs and release controls"
    )
    evidence = _workflow_step(deploy, "Assemble non-secret staging release evidence")

    assert f'"${CONTROL}" != "false"' in publication
    assert f'"${CONTROL}" != "false"' in identity
    assert f".flags.{CONTROL} // empty" in deploy
    assert '"$observed_sentry_staging_enabled" == "false"' in evidence
    assert f"{CONTROL}: $sentry_staging_enabled" in deploy
    assert "secrets.SENTRY_DSN" not in workflow

    attempt = _workflow_job(workflow, "attempt_evidence")
    terminal = _workflow_step(attempt, "Require a complete successful staging attempt")
    assert f'.flags.{CONTROL} == "false"' in terminal
    assert f'.publicConfig.{CONTROL} == "false"' in terminal
