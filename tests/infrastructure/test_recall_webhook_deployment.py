from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET = "RECALL_WEBHOOK_SECRET"
FLAG = "DURABLE_WEBHOOK_INBOX_ENABLED"


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
    start = source.index(f"{name}=(")
    end = source.index("\n)", start)
    return source[start : end + 2]


def test_recall_signing_secret_is_api_only_and_flag_is_public() -> None:
    for directory in ("infra", "infra-staging"):
        variables = _read(f"{directory}/variables.tf")
        common = _hcl_block(variables, 'variable "python_secret_vars"')
        api_only = _hcl_block(variables, 'variable "python_api_only_secret_vars"')
        public = _hcl_block(variables, 'variable "python_public_vars"')
        assert SECRET not in common
        assert api_only.count(SECRET) == 1
        assert public.count(FLAG) == 1

    production = _read("infra/main.tf")
    api = _hcl_block(production, 'resource "azurerm_container_app" "app"')
    assert api.count("var.python_api_only_secret_vars") == 2
    for workload in ("scheduler", "worker", "job-monitor"):
        block = _hcl_block(production, f'resource "azurerm_container_app" "{workload}"')
        assert "python_api_only_secret_vars" not in block
        assert SECRET not in block

    deploy = _read("infra/deploy.sh")
    assert SECRET not in _shell_array(deploy, "COMMON_SECRET_VARS")
    assert deploy.index("api_only_secret_live_guard.py") < deploy.index(
        "\nrun_migrations\n"
    )


def test_staging_scheduler_and_cutover_are_fail_closed_before_migrations() -> None:
    main = _read("infra-staging/main.tf")
    scheduler = _hcl_block(main, 'resource "azurerm_container_app" "scheduler"')
    assert "min_replicas = 1" in scheduler
    assert "max_replicas = 1" in scheduler
    assert 'args   = [data.external.procfile.result["scheduler"]]' in scheduler
    assert "var.python_secret_vars" in scheduler
    assert "python_api_only_secret_vars" not in scheduler
    assert "readiness_probe" not in scheduler
    assert "liveness_probe" not in scheduler

    deploy = _read("infra-staging/deploy.sh")
    assert 'SCHEDULER_CONTAINER="cosmaneura-staging-scheduler"' in deploy
    assert "SCHEDULER_COMMAND=$(yq" in deploy
    assert "for container in $CONTAINERS" in deploy
    preflight = deploy.index("for container in $CONTAINERS")
    migration = deploy.index("\nrun_migrations\n")
    assert preflight < migration
    assert '--args "$SCHEDULER_COMMAND"' in deploy
    assert "RunningAtMaxScale" in deploy
    assert SECRET not in _shell_array(deploy, "COMMON_SECRET_VARS")
    assert _shell_array(deploy, "API_ONLY_SECRET_VARS").count(SECRET) == 1
    assert deploy.index("for container in $BACKEND_CONTAINERS") < deploy.index(
        'initiate_deployment "$API_CONTAINER"'
    )


def test_secret_sources_are_references_or_protected_workflow_values() -> None:
    production = _read(".env.production.backend")
    staging = _read(".env.staging.backend")
    assert "RECALL_WEBHOOK_SECRET_INPUT" in production
    assert "RECALL_WEBHOOK_SECRET_INPUT" in staging
    assert "whsec_" not in production
    assert "whsec_" not in staging
    assert f"export {FLAG}" in production
    assert f"export {FLAG}" in staging

    workflow = _read(".github/workflows/build_and_publish.yml")
    assert f"{SECRET}: ${{{{ secrets.{SECRET} }}}}" in workflow
    assert f"vars.{FLAG} || 'false'" in workflow
