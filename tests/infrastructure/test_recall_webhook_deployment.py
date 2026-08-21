from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET = "RECALL_WEBHOOK_SECRET"
FLAG = "DURABLE_WEBHOOK_INBOX_ENABLED"
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


def _workflow_job(source: str, name: str) -> str:
    header = f"  {name}:\n"
    start = source.index(header)
    next_job = re.search(r"^  [a-z0-9-]+:\n", source[start + len(header) :], re.M)
    if next_job is None:
        return source[start:]
    end = start + len(header) + next_job.start()
    return source[start:end]


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


def test_pull_request_plan_is_secretless_and_cannot_read_production_state() -> None:
    plan = _read(".github/workflows/infra_plan.yml")
    production_plan = _read(".github/workflows/infra_production_plan.yml")
    static_plan = _read("infra/tests/secretless_plan.tftest.hcl")
    workflow_scope = plan[: plan.index("jobs:\n")]
    pr_plan = _workflow_job(plan, "pr-static-plan")
    trusted_plan = _workflow_job(production_plan, "plan")

    assert "  pull_request:" in plan
    assert "workflow_dispatch" not in plan
    assert "  contents: read" in workflow_scope
    assert "id-token:" not in workflow_scope
    assert "issues:" not in workflow_scope

    for forbidden in (
        "${{ secrets.",
        "id-token: write",
        "azure/login@",
        "OP_SERVICE_ACCOUNT_TOKEN:",
        "ARM_USE_OIDC:",
        "ARM_USE_AZUREAD:",
        "ARM_CLIENT_ID:",
        "ARM_TENANT_ID:",
        "ARM_SUBSCRIPTION_ID:",
    ):
        assert forbidden not in plan

    assert "if: github.event_name == 'pull_request'" in pr_plan
    assert "persist-credentials: false" in pr_plan
    assert "tofu init -backend=false -input=false" in pr_plan
    assert "tofu test -test-directory=tests -no-color" in pr_plan
    assert 'mock_provider "azurerm"' in static_plan
    assert 'mock_provider "external"' in static_plan
    assert "target = data.external.secrets" in static_plan
    assert "target = data.external.database_url_live_guard" in static_plan
    assert "target = data.terraform_remote_state.tfstate" in static_plan
    assert "op://" not in static_plan
    assert "${{" not in static_plan
    assert "tofu plan -no-color" not in pr_plan
    assert "issues: write" not in pr_plan
    assert "GITHUB_STEP_SUMMARY" in pr_plan
    assert "actions/upload-artifact" not in pr_plan
    assert "Post plan as PR comment" not in plan
    assert "issues: write" not in plan
    assert "pull-requests: write" not in plan

    assert "pull_request" not in production_plan
    assert "  workflow_dispatch: {}" in production_plan
    assert "github.ref == 'refs/heads/master'" in trusted_plan
    assert "environment: production" in trusted_plan
    assert "id-token: write" in trusted_plan
    assert "azure/login@v3" in trusted_plan
    assert "tofu init -input=false" in trusted_plan
    assert "tofu plan -no-color" in trusted_plan
    assert "OP_SERVICE_ACCOUNT_TOKEN: ${{ secrets.OP_SERVICE_ACCOUNT_TOKEN }}" in (
        trusted_plan
    )
    assert f"{SECRET}: ${{{{ secrets.{SECRET} }}}}" in trusted_plan
    for credential in (
        "ARM_CLIENT_ID",
        "ARM_TENANT_ID",
        "ARM_SUBSCRIPTION_ID",
    ):
        assert f"{credential}: ${{{{ vars.AZURE_" in trusted_plan


def test_pull_request_build_jobs_have_no_secret_or_cloud_capabilities() -> None:
    workflow = _read(".github/workflows/pull_request_checks.yml")
    publish_workflow = _read(".github/workflows/build_and_publish.yml")
    workflow_scope = workflow[: workflow.index("jobs:\n")]
    pull_request_jobs = {name: _workflow_job(workflow, name) for name in ("app", "web")}
    protected_jobs = {
        "deploy": _workflow_job(publish_workflow, "deploy"),
        "deploy-staging": _workflow_job(publish_workflow, "deploy-staging"),
    }

    assert "  pull_request: {}" in workflow_scope
    publish_trigger = publish_workflow[: publish_workflow.index("concurrency:\n")]
    assert "pull_request:" not in publish_trigger
    assert "  contents: read" in workflow_scope
    for forbidden in (
        "${{ secrets.",
        "id-token: write",
        "azure/login@",
        "OP_SERVICE_ACCOUNT_TOKEN",
        "CLERK_PRIVATE_KEY",
        "RECALL_WEBHOOK_SECRET",
        "ARM_CLIENT_ID",
        "ARM_TENANT_ID",
        "ARM_SUBSCRIPTION_ID",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
    ):
        assert forbidden not in workflow

    for job in pull_request_jobs.values():
        assert "${{ secrets." not in job
        assert "id-token: write" not in job
        assert "azure/login@" not in job
        assert "OP_SERVICE_ACCOUNT_TOKEN:" not in job
        assert "CLERK_PRIVATE_KEY:" not in job
        assert "RECALL_WEBHOOK_SECRET:" not in job
        assert "AZURE_CLIENT_ID:" not in job
        assert "AZURE_TENANT_ID:" not in job
        assert "AZURE_SUBSCRIPTION_ID:" not in job
        assert "ARM_CLIENT_ID:" not in job
        assert "ARM_TENANT_ID:" not in job
        assert "ARM_SUBSCRIPTION_ID:" not in job
        assert "environment: production" not in job
        assert "environment: staging" not in job

    for job in pull_request_jobs.values():
        for checkout in re.finditer(r"uses: actions/checkout@v5", job):
            next_step = job.find("\n      - ", checkout.end())
            step = job[checkout.start() : next_step if next_step != -1 else None]
            assert "persist-credentials: false" in step

    production = protected_jobs["deploy"]
    staging = protected_jobs["deploy-staging"]
    assert "github.ref == 'refs/heads/master'" in production
    assert "environment: production" in production
    assert "github.ref == 'refs/heads/staging'" in staging
    assert "github.event_name == 'push'" in staging
    assert "environment: staging" in staging
    for protected_job in protected_jobs.values():
        assert "id-token: write" in protected_job
        assert "azure/login@v3" in protected_job
        assert "OP_SERVICE_ACCOUNT_TOKEN: ${{ secrets.OP_SERVICE_ACCOUNT_TOKEN }}" in (
            protected_job
        )
        assert "CLERK_PRIVATE_KEY: ${{ secrets.CLERK_PRIVATE_KEY }}" in protected_job
        assert f"{SECRET}: ${{{{ secrets.{SECRET} }}}}" in protected_job


def test_every_pull_request_workflow_is_credential_free() -> None:
    workflows = ROOT / ".github/workflows"
    pull_request_workflows = []
    workflow_paths = [*workflows.glob("*.yml"), *workflows.glob("*.yaml")]
    for path in sorted(workflow_paths):
        source = path.read_text(encoding="utf-8")
        trigger = (
            source[: source.index("permissions:")]
            if "permissions:" in source
            else source
        )
        if re.search(r"(?m)^  pull_request(?:_target)?:", trigger):
            pull_request_workflows.append((path.name, source))

    assert {name for name, _ in pull_request_workflows} == {
        "infra_plan.yml",
        "pull_request_checks.yml",
    }
    forbidden = (
        "${{ secrets.",
        "id-token: write",
        "azure/login@",
        "OP_SERVICE_ACCOUNT_TOKEN",
        "CLERK_PRIVATE_KEY",
        "RECALL_WEBHOOK_SECRET",
        "ARM_CLIENT_ID",
        "ARM_TENANT_ID",
        "ARM_SUBSCRIPTION_ID",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "backend-config",
    )
    for name, source in pull_request_workflows:
        for value in forbidden:
            assert value not in source, f"{name} contains forbidden capability {value}"


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

    plan_workflow = _read(".github/workflows/infra_production_plan.yml")
    plan_job = _workflow_job(plan_workflow, "plan")
    assert "    environment: production" in plan_job
    assert plan_job.count(protected_reference) == 1

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
    production_plan = _read(".github/workflows/infra_production_plan.yml")
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

    assert fixed_false not in plan_workflow
    assert mutable_default not in plan_workflow
    assert f"vars.{FLAG}" not in plan_workflow

    for protected_job in (
        production_plan,
        apply_workflow,
        production_deploy,
        staging_deploy,
    ):
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
