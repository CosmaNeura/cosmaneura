from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET = "RECALL_WEBHOOK_SECRET"
FLAG = "DURABLE_WEBHOOK_INBOX_ENABLED"
PLAN_ONLY_SECRET = "whsec_cHItcGxhbi1ub25zZWNyZXQtZG8tbm90LXVzZQ=="
PROTECTED_FLAG = f"PROTECTED_{FLAG}"


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


def test_pull_request_plan_uses_only_a_fixed_nonsecret_recall_value() -> None:
    plan = _read(".github/workflows/infra_plan.yml")
    protected_reference = f"${{{{ secrets.{SECRET} }}}}"

    assert "  pull_request:" in plan
    assert protected_reference not in plan
    assert f"secrets.{SECRET}" not in plan
    assert plan.count(f'{SECRET}: "{PLAN_ONLY_SECRET}"') == 1
    assert plan.count(f'{FLAG}: "false"') == 1

    prefix, encoded = PLAN_ONLY_SECRET.split("_", maxsplit=1)
    assert prefix == "whsec"
    assert base64.b64decode(encoded, validate=True) == b"pr-plan-nonsecret-do-not-use"


def test_secret_sources_are_references_or_protected_workflow_values() -> None:
    production = _read(".env.production.backend")
    staging = _read(".env.staging.backend")
    assert "RECALL_WEBHOOK_SECRET_INPUT" in production
    assert "RECALL_WEBHOOK_SECRET_INPUT" in staging
    assert "whsec_" not in production
    assert "whsec_" not in staging
    assert f"export {FLAG}" in production
    assert f"export {FLAG}" in staging

    protected_reference = f"{SECRET}: ${{{{ secrets.{SECRET} }}}}"

    apply_workflow = _read(".github/workflows/infra_apply.yml")
    apply_job = apply_workflow[apply_workflow.index("  apply:\n") :]
    assert "    environment: production" in apply_job
    assert apply_job.count(protected_reference) == 1

    publish_workflow = _read(".github/workflows/build_and_publish.yml")
    production_deploy = publish_workflow[
        publish_workflow.index("  deploy:\n") : publish_workflow.index(
            "  deploy-staging:\n"
        )
    ]
    staging_deploy = publish_workflow[publish_workflow.index("  deploy-staging:\n") :]
    assert "    environment: production" in production_deploy
    assert production_deploy.count(protected_reference) == 1
    assert "    environment: staging" in staging_deploy
    assert staging_deploy.count(protected_reference) == 1


def test_cutover_flag_is_source_locked_false_across_plan_apply_and_deploy() -> None:
    plan_workflow = _read(".github/workflows/infra_plan.yml")
    apply_workflow = _read(".github/workflows/infra_apply.yml")
    publish_workflow = _read(".github/workflows/build_and_publish.yml")
    production_deploy = publish_workflow[
        publish_workflow.index("  deploy:\n") : publish_workflow.index(
            "  deploy-staging:\n"
        )
    ]
    staging_deploy = publish_workflow[publish_workflow.index("  deploy-staging:\n") :]
    fixed_false = f'{FLAG}: "false"'
    mutable_default = f"{FLAG}: ${{{{ vars.{FLAG} || 'false' }}}}"
    protected_probe = f"{PROTECTED_FLAG}: ${{{{ vars.{FLAG} }}}}"
    presence_check = f'-n "${{{PROTECTED_FLAG}:-}}"'
    rejection = f'"${PROTECTED_FLAG}" != "false"'

    assert plan_workflow.count(fixed_false) == 1
    assert mutable_default not in plan_workflow
    assert f"vars.{FLAG}" not in plan_workflow

    for protected_job in (apply_workflow, production_deploy, staging_deploy):
        assert protected_job.count(fixed_false) == 1
        assert mutable_default not in protected_job
        assert protected_job.count(f"vars.{FLAG}") == 1
        assert protected_job.count(protected_probe) == 1
        assert protected_job.count(presence_check) == 1
        assert protected_job.count(rejection) == 1
        assert "activation requires a reviewed workflow change" in protected_job
        assert protected_job.index("Reject unreviewed durable webhook activation") < (
            protected_job.index("actions/checkout")
        )
