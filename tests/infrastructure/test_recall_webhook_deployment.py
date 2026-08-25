from __future__ import annotations

import json
import re
import subprocess
import sys
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


def _workflow_run_sources(source: str) -> list[str]:
    lines = source.splitlines()
    run_sources: list[str] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)run:\s*(.*?)\s*$", lines[index])
        if match is None:
            index += 1
            continue

        indentation = len(match.group(1))
        scalar = match.group(2)
        index += 1
        if scalar not in {"|", "|-", ">", ">-"}:
            run_sources.append(scalar)
            continue

        body: list[str] = []
        while index < len(lines):
            line = lines[index]
            line_indentation = len(line) - len(line.lstrip())
            if line.strip() and line_indentation <= indentation:
                break
            body.append(line)
            index += 1
        run_sources.append("\n".join(body))
    return run_sources


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


def test_production_rollout_verifies_consumers_before_api_and_jmon() -> None:
    deploy = _read("infra/deploy.sh")

    assert (
        'WORKER_SCHEDULER_CONTAINERS="cosmaneura-prod-worker '
        'cosmaneura-prod-scheduler"' in deploy
    )
    assert 'API_CONTAINER="cosmaneura-prod-app"' in deploy
    assert 'JMON_CONTAINER="cosmaneura-prod-app-jmon"' in deploy

    migration = deploy.index("\nrun_migrations\n")
    consumers = deploy.index("for container in $WORKER_SCHEDULER_CONTAINERS")
    consumer_check = deploy.index('check_deployment "$container" "$GIT_SHA"', consumers)
    api_start = deploy.index('initiate_deployment "$API_CONTAINER"', consumer_check)
    api_check = deploy.index('check_deployment "$API_CONTAINER"', api_start)
    jmon_start = deploy.index('initiate_deployment "$JMON_CONTAINER"', api_check)
    jmon_check = deploy.index('check_deployment "$JMON_CONTAINER"', jmon_start)

    assert migration < consumers < consumer_check < api_start < api_check
    assert api_check < jmon_start < jmon_check


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


def test_staging_recall_secret_validation_precedes_all_azure_mutation() -> None:
    deploy = _read("infra-staging/deploy.sh")
    validation = deploy.index("--validate-signing-secret-only")

    assert "api_only_secret_live_guard.py" in deploy
    assert 'RECALL_WEBHOOK_SECRET="$RECALL_WEBHOOK_SECRET_INPUT"' in deploy
    assert 'type == "string" and length > 0' not in deploy
    assert "Staging Recall webhook signing credential is invalid." in deploy
    assert deploy.index("unset RECALL_WEBHOOK_SECRET_INPUT") > validation
    assert validation < deploy.index("\nrun_migrations\n")
    for mutation in (
        "az containerapp job delete",
        "az containerapp job create",
        "az containerapp job start",
        "az containerapp secret set",
        "az containerapp secret remove",
        "az containerapp update",
    ):
        assert validation < deploy.index(mutation)


def test_deploy_scripts_drop_ambient_provider_credentials_before_subprocesses() -> None:
    cases = (
        ("infra/deploy.sh", ".env.production.backend"),
        ("infra-staging/deploy.sh", ".env.staging.backend"),
    )

    for path, environment_file in cases:
        deploy = _read(path)
        resolution = deploy.index(f"just direnv_export {environment_file}")
        credential_drop = deploy.index(
            "unset OP_SERVICE_ACCOUNT_TOKEN RECALL_WEBHOOK_SECRET",
            resolution,
        )
        first_git = deploy.index("if ! git rev-parse", credential_drop)
        first_azure = deploy.index("az account show", credential_drop)
        first_docker = deploy.index("docker manifest inspect", credential_drop)

        assert resolution < credential_drop
        assert credential_drop < first_git
        assert credential_drop < first_azure
        assert credential_drop < first_docker
        assert "export ENV_JSON" not in deploy

    production = _read("infra/deploy.sh")
    guard_assignment = production.index("RECALL_WEBHOOK_SECRET_GUARD=")
    guard_invocation = production.index(
        'RECALL_WEBHOOK_SECRET="$RECALL_WEBHOOK_SECRET_GUARD"', guard_assignment
    )
    guard_drop = production.index("unset RECALL_WEBHOOK_SECRET_GUARD", guard_invocation)
    assert guard_assignment < guard_invocation < guard_drop

    staging = _read("infra-staging/deploy.sh")
    guard_assignment = staging.index("RECALL_WEBHOOK_SECRET_INPUT=")
    guard_invocation = staging.index(
        'RECALL_WEBHOOK_SECRET="$RECALL_WEBHOOK_SECRET_INPUT"', guard_assignment
    )
    guard_drop = staging.index("unset RECALL_WEBHOOK_SECRET_INPUT", guard_invocation)
    assert guard_assignment < guard_invocation < guard_drop


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
    assert re.search(r"azure/login@[0-9a-f]{40}", trusted_plan)
    assert "tofu init -input=false" in trusted_plan
    assert "tofu plan -no-color" in trusted_plan
    assert (
        "OP_SERVICE_ACCOUNT_TOKEN: "
        "${{ secrets.OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN }}" in trusted_plan
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
    assert (
        "VITE_CLERK_PUBLIC_KEY: "
        "pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk" in workflow_scope
    )
    assert "secrets.VITE_CLERK_PUBLIC_KEY" not in workflow
    assert "secrets.CLERK_PUBLISHABLE_KEY" not in workflow
    publish_trigger = publish_workflow[: publish_workflow.index("concurrency:\n")]
    assert "pull_request:" not in publish_trigger
    publish_global_scope = publish_workflow[: publish_workflow.index("jobs:\n")]
    assert (
        "VITE_CLERK_PUBLIC_KEY: "
        "pk_test_Y2xlcmsucHVsbC1yZXF1ZXN0LmludmFsaWQk" in publish_global_scope
    )
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
        assert re.search(r"azure/login@[0-9a-f]{40}", protected_job)
        assert f"{SECRET}: ${{{{ secrets.{SECRET} }}}}" in protected_job
        job_environment = protected_job[: protected_job.index("    steps:\n")]
        assert "OP_SERVICE_ACCOUNT_TOKEN:" not in job_environment
        assert "RECALL_WEBHOOK_SECRET:" not in job_environment
        assert "CLERK_PRIVATE_KEY:" not in job_environment
    assert (
        "OP_SERVICE_ACCOUNT_TOKEN: "
        "${{ secrets.OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN }}" in production
    )
    assert (
        "OP_SERVICE_ACCOUNT_TOKEN: "
        "${{ secrets.OP_STAGING_SERVICE_ACCOUNT_TOKEN }}" in staging
    )
    assert production.count("secrets.OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN") == 2
    assert staging.count("secrets.OP_STAGING_SERVICE_ACCOUNT_TOKEN") == 2
    assert "CLERK_PRIVATE_KEY: ${{ secrets.CLERK_PRIVATE_KEY }}" not in publish_workflow
    assert "secrets.OP_SERVICE_ACCOUNT_TOKEN" not in publish_workflow


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


def test_production_apply_is_manual_and_bound_to_exact_plan_and_master() -> None:
    apply_workflow = _read(".github/workflows/infra_apply.yml")
    apply_job = _workflow_job(apply_workflow, "apply")
    trigger = apply_workflow[: apply_workflow.index("permissions:\n")]

    assert "  push:" not in trigger
    assert "  workflow_dispatch:" in trigger
    assert "production_plan_run_id:" in trigger
    assert "production_plan_artifact_digest:" in trigger
    assert "required: true" in trigger
    assert "github.ref == 'refs/heads/master'" in apply_job
    assert "needs.authorize-production.result == 'success'" in apply_job
    assert "environment: production" in apply_job
    assert "actions: read" in apply_job
    assert "actions: write" not in apply_job
    assert "persist-credentials: false" in apply_job
    assert "repos/$GITHUB_REPOSITORY/git/ref/heads/master" in apply_job
    assert "repos/$GITHUB_REPOSITORY/actions/runs/$PRODUCTION_PLAN_RUN_ID" in apply_job
    assert "actions/runs/$PRODUCTION_PLAN_RUN_ID/artifacts?per_page=100" in apply_job
    assert "artifact_digest" in apply_job
    assert 'artifact_count" != "1"' in apply_job
    assert "actions/download-artifact@" in apply_job
    assert "reviewed-attestation/plan-attestation.json" in apply_job
    for evidence in (
        '"Terraform Production Plan"',
        '".github/workflows/infra_production_plan.yml"',
        '"workflow_dispatch"',
        '"completed"',
        '"success"',
        '"master"',
        '"$TARGET_SHA"',
    ):
        assert evidence in apply_job
    assert apply_job.count("git/ref/heads/master") == 2
    assert apply_job.index("Verify exact-current-master production plan evidence") < (
        apply_job.index("Recreate and verify exact reviewed plan")
    )
    assert "tofu show -json tfplan.bin" in apply_job
    assert "canonical_plan_sha256" in apply_job
    assert "configuration_sha256" in apply_job
    assert "provider_lock_sha256" in apply_job
    assert "tofu_version: 1.12.5" in apply_job
    assert "tofu init -input=false -lockfile=readonly" in apply_job
    assert "state_lineage" in apply_job
    assert "state_serial" in apply_job
    assert apply_job.index("Reverify current master and apply") < (
        apply_job.index("tofu apply")
    )
    assert 'echo "run_id=$PRODUCTION_PLAN_RUN_ID" >> "$GITHUB_OUTPUT"' in apply_job
    assert (
        'echo "artifact_digest=$PRODUCTION_PLAN_ARTIFACT_DIGEST" '
        '>> "$GITHUB_OUTPUT"' in apply_job
    )
    summary_start = apply_job.index("Summarize apply")
    summary_end = apply_job.index("\n      - name:", summary_start)
    summary_step = apply_job[summary_start:summary_end]
    assert "steps.plan_evidence.outputs.run_id" in summary_step
    assert "steps.plan_evidence.outputs.artifact_digest" in summary_step
    assert '"Reviewed plan run: $REVIEWED_PLAN_RUN_ID"' in summary_step
    assert '"Attestation digest: $ATTESTATION_DIGEST"' in summary_step


def test_workflow_inputs_are_never_interpolated_into_shell_source() -> None:
    workflow_paths = sorted((ROOT / ".github/workflows").glob("*.yml"))
    action_paths = sorted((ROOT / ".github/actions").glob("*/action.yml"))

    for path in (*workflow_paths, *action_paths):
        for run_source in _workflow_run_sources(path.read_text(encoding="utf-8")):
            assert (
                re.search(r"\$\{\{\s*(?:github\.event\.)?inputs\.", run_source) is None
            ), path


def test_production_plan_uploads_only_nonsecret_immutable_attestation() -> None:
    plan_workflow = _read(".github/workflows/infra_production_plan.yml")
    plan_start = plan_workflow.index("tofu plan against protected production state")
    plan_end = plan_workflow.index("\n      - name:", plan_start)
    plan_step = plan_workflow[plan_start:plan_end]
    upload_start = plan_workflow.index("Upload immutable reviewed-plan attestation")
    upload_end = plan_workflow.index("\n      - name:", upload_start)
    upload_step = plan_workflow[upload_start:upload_end]
    digest_start = plan_workflow.index("Record immutable attestation digest")
    digest_end = plan_workflow.index("\n      - name:", digest_start)
    digest_step = plan_workflow[digest_start:digest_end]

    assert "tofu show -json tfplan.bin" in plan_workflow
    assert 'case "$plan_exit" in' in plan_step
    assert "0 | 2) ;;" in plan_step
    assert "OpenTofu plan returned an unexpected exit status." in plan_step
    assert 'if [ "$plan_exit" -eq 1 ]' not in plan_step
    canonical_filter = (
        "del(.timestamp) | .relevant_attributes |= "
        "((. // []) | sort_by(.resource, .attribute))"
    )
    canonical_command = f"jq -cS '{canonical_filter}'"
    assert canonical_command in plan_workflow
    assert canonical_command in _read(".github/workflows/infra_apply.yml")

    plan_a = {
        "timestamp": "first",
        "resource_changes": [{"address": "example.stable"}],
        "relevant_attributes": [
            {"resource": "example.same", "attribute": ["a.b"]},
            {"resource": "example.same", "attribute": ["a", "b"]},
            {"resource": "example.same", "attribute": [0]},
            {"resource": "example.same", "attribute": ["0"]},
        ],
    }
    plan_b = {
        **plan_a,
        "timestamp": "second",
        "relevant_attributes": list(reversed(plan_a["relevant_attributes"])),
    }

    def canonicalize(plan: dict[str, object]) -> str:
        return subprocess.run(
            ["jq", "-cS", canonical_filter],
            check=True,
            capture_output=True,
            input=json.dumps(plan),
            text=True,
        ).stdout

    assert canonicalize(plan_a) == canonicalize(plan_b)
    assert canonicalize(plan_a) != canonicalize(
        {**plan_a, "resource_changes": [{"address": "example.changed"}]}
    )
    assert canonicalize({"timestamp": "first"}) == canonicalize(
        {"timestamp": "second", "relevant_attributes": None}
    )
    assert "configuration_sha256" in plan_workflow
    assert "provider_lock_sha256" in plan_workflow
    assert "tofu_version: 1.12.5" in plan_workflow
    assert "tofu init -input=false -lockfile=readonly" in plan_workflow
    assert "state_lineage" in plan_workflow
    assert "state_serial" in plan_workflow
    assert "path: infra/plan-attestation.json" in upload_step
    assert "retention-days: 1" in upload_step
    assert "overwrite: false" in upload_step
    assert "tfplan.bin" not in upload_step
    assert "rm -f infra/tfplan.bin" in plan_workflow
    assert plan_workflow.index("rm -f tfplan.bin plan.txt") < upload_start
    assert 'artifact_digest="${ARTIFACT_DIGEST#sha256:}"' in digest_step
    assert '[[ ! "$artifact_digest" =~ ^[0-9a-f]{64}$ ]]' in digest_step
    assert 'normalized_artifact_digest="sha256:$artifact_digest"' in digest_step
    assert (
        'echo "artifact_digest=$normalized_artifact_digest" >> "$GITHUB_OUTPUT"'
        in digest_step
    )
    assert "Artifact service digest: \\`$normalized_artifact_digest\\`" in digest_step
    assert '[[ ! "$ARTIFACT_DIGEST" =~ ^sha256:' not in digest_step


def test_changed_workflow_actions_are_immutably_pinned() -> None:
    for path in (
        ".github/actions/common-setup/action.yml",
        ".github/workflows/build_and_publish.yml",
        ".github/workflows/infra_apply.yml",
        ".github/workflows/infra_plan.yml",
        ".github/workflows/infra_production_plan.yml",
        ".github/workflows/pull_request_checks.yml",
    ):
        source = _read(path)
        external_actions = re.findall(r"uses:\s+(?!\./)([^@\s]+)@([^\s#]+)", source)
        assert external_actions, path
        for action, revision in external_actions:
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (
                f"{path} leaves {action}@{revision} mutable"
            )


def test_privileged_build_uses_only_checksum_verified_pinned_nixpacks() -> None:
    installer = _read(".github/actions/install-nixpacks/action.yml")
    build_recipe = _read("just/build.just")
    publish = _read(".github/workflows/build_and_publish.yml")
    production_deploy = _workflow_job(publish, "deploy")
    staging_deploy = _workflow_job(publish, "deploy-staging")

    assert "NIXPACKS_VERSION: 1.41.0" in installer
    assert (
        "NIXPACKS_ARCHIVE_SHA256: "
        "194bcad8c379f78a309eee1a88b2e6b2abc59f354efe7ecd7b4bbaf21de99a06" in installer
    )
    assert "releases/download/v${NIXPACKS_VERSION}" in installer
    assert "sha256sum --check --strict" in installer
    assert installer.index("curl \\") < installer.index("sha256sum --check --strict")
    assert installer.index("sha256sum --check --strict") < installer.index(
        'tar -xzf "$archive"'
    )
    assert 'install_root=$(mktemp -d "$RUNNER_TEMP/nixpacks.XXXXXX")' in installer
    assert 'chmod 0700 "$install_root"' in installer
    assert 'archive_type=$(tar -tvzf "$archive" | cut -c1)' in installer
    assert '"$archive_type" != "-"' in installer
    assert '[[ ! -f "$bin_dir/nixpacks" || -L "$bin_dir/nixpacks" ]]' in installer
    assert "nixpacks.com/install.sh" not in build_recipe
    assert "command -v nixpacks" in build_recipe

    for deploy_job in (production_deploy, staging_deploy):
        install = deploy_job.index("uses: ./.github/actions/install-nixpacks")
        registry_login = deploy_job.index("uses: docker/login-action@")
        build = deploy_job.index("run: just build\n")
        first_secret = deploy_job.index("${{ secrets.")
        assert install < registry_login < build
        assert install < first_secret

    for workflow in (
        ".github/workflows/infra_production_plan.yml",
        ".github/workflows/infra_apply.yml",
    ):
        source = _read(workflow)
        hash_start = source.index("configuration_sha256=$(")
        hash_end = source.index("provider_lock_sha256=", hash_start)
        configuration_scope = source[hash_start:hash_end]
        assert "Justfile just Procfile .github/actions" in configuration_scope
        assert ".github/workflows/build_and_publish.yml" in configuration_scope

    privileged_paths = (
        ".github/actions/common-setup/action.yml",
        ".github/actions/install-nixpacks/action.yml",
        ".github/actions/install-op/action.yml",
        ".github/workflows/build_and_publish.yml",
        ".github/workflows/infra_apply.yml",
        ".github/workflows/infra_production_plan.yml",
        "infra/deploy.sh",
        "infra-staging/deploy.sh",
        "just/build.just",
    )
    download_sources: list[str] = []
    for path in privileged_paths:
        source = _read(path)
        assert re.search(r"(?:curl|wget)[^\n]*\|\s*(?:ba)?sh\b", source) is None
        if re.search(r"\b(?:curl|wget)\b", source):
            download_sources.append(path)
    assert download_sources == [
        ".github/actions/install-nixpacks/action.yml",
        ".github/actions/install-op/action.yml",
    ]


def test_build_push_braces_registry_image_before_tags_for_zsh() -> None:
    build_recipe = _read("just/build.just")
    build_push = build_recipe[
        build_recipe.index("build_push:") : build_recipe.index(
            "\n# dump json output", build_recipe.index("build_push:")
        )
    ]

    assert build_push.count('"${registry_image}:{{GIT_SHA}}"') == 2
    assert build_push.count('"${registry_image}:latest"') == 2
    assert '"$registry_image:' not in build_push


def test_onepassword_cli_installer_is_exact_and_checksum_verified(
    tmp_path: Path,
) -> None:
    installer_path = ".github/actions/install-op/action.yml"
    installer = _read(installer_path)
    common_setup = _read(".github/actions/common-setup/action.yml")
    expected_sha256 = "3d5230a1a57f6b7259a837e345fb7886614e20cd87b53640fa4a93c67c0b2bf1"

    assert "uses: ./.github/actions/install-op" in common_setup
    assert "1password/load-secrets-action" not in common_setup
    assert "OP_CLI_PACKAGE_VERSION: 2.39.0-1" in installer
    assert "OP_CLI_VERSION: 2.39.0" in installer
    assert f"OP_CLI_DEB_SHA256: {expected_sha256}" in installer
    assert "downloads.1password.com/linux/debian/amd64/" in installer
    assert "1password-cli-${OP_CLI_PACKAGE_VERSION}.amd64.deb" in installer
    assert installer.index("sha256sum --check --strict") < installer.index(
        "dpkg-deb --field"
    )
    assert installer.index("dpkg-deb --field") < installer.index("dpkg-deb --extract")
    assert '"$package_name" != "1password-cli"' in installer
    assert '"$package_version" != "$OP_CLI_PACKAGE_VERSION"' in installer
    assert '"$package_architecture" != "amd64"' in installer
    assert '[[ ! -f "$op_binary" || -L "$op_binary" ]]' in installer
    assert '"$("$install_dir/op" --version)" != "$OP_CLI_VERSION"' in installer

    tampered_package = tmp_path / "1password-cli.deb"
    tampered_package.write_bytes(b"not the pinned package")
    rejected = subprocess.run(
        ["sha256sum", "--check", "--strict"],
        input=f"{expected_sha256}  {tampered_package}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0


def test_production_deploy_rechecks_current_master_before_mutation() -> None:
    publish = _read(".github/workflows/build_and_publish.yml")
    production_deploy = _workflow_job(publish, "deploy")

    publish_check = production_deploy.index(
        "Reverify current master before publishing production images"
    )
    image_publish = production_deploy.index("run: just build_push")
    deploy_check = production_deploy.index(
        "Reverify current master before production deployment"
    )
    azure_mutation = production_deploy.index("name: Deploy to Azure")

    assert publish_check < image_publish < deploy_check < azure_mutation
    assert (
        production_deploy.count('"repos/$GITHUB_REPOSITORY/git/ref/heads/master"') == 2
    )
    assert (
        production_deploy.count('if [[ "$current_master_sha" != "$TARGET_SHA" ]]') == 2
    )
    final_check = production_deploy[deploy_check:azure_mutation]
    assert "GH_TOKEN: ${{ github.token }}" in final_check
    assert "TARGET_SHA: ${{ github.sha }}" in final_check
    assert "${{ secrets." not in final_check


def test_onepassword_exports_are_direct_non_caching_and_daemon_free() -> None:
    direnv_recipe = _read("just/direnv.just")

    assert "op daemon" not in direnv_recipe
    assert direnv_recipe.count("OP_CACHE=false") == 2
    assert direnv_recipe.count('env -i HOME="$HOME" PATH="$PATH" OP_CACHE=false') == 2
    assert (
        direnv_recipe.count('OP_SERVICE_ACCOUNT_TOKEN="${OP_SERVICE_ACCOUNT_TOKEN:-}"')
        == 2
    )
    envrc = _read(".envrc")
    assert 'chmod 0600 "$tmpfile"' in envrc
    assert envrc.count('rm -f "$tmpfile"') == 3


def test_protected_environment_files_use_only_their_own_vault() -> None:
    protected_files = {
        "production": (
            "OP_PROD_VAULT_UID",
            (".env.production.backend", ".env.production.frontend"),
        ),
        "staging": (
            "OP_STAGING_VAULT_UID",
            (".env.staging.backend", ".env.staging.frontend"),
        ),
    }

    assert "op://" not in _read(".env")
    for environment, (expected_vault, paths) in protected_files.items():
        for path in paths:
            source = _read(path)
            declared_vaults = set(
                re.findall(r"^export (OP_[A-Z]+_VAULT_UID)=", source, re.MULTILINE)
            )
            referenced_vaults = set(
                re.findall(r"op://\$\{(OP_[A-Z]+_VAULT_UID)\}/", source)
            )
            assert declared_vaults == {expected_vault}, (
                f"{path} declares a vault outside {environment}"
            )
            assert referenced_vaults == {expected_vault}, (
                f"{path} references a vault outside {environment}"
            )

    for path in (".env.production.frontend", ".env.staging.frontend"):
        source = _read(path)
        assert "op_inject_source <<'EOF'" in source
        assert "if op whoami" not in source
        assert "source <(" not in source
        assert "pk_test_placeholder" not in source
        assert "SENTRY_AUTH_TOKEN" not in source
        assert "SENTRY_ORG" not in source
        assert "SENTRY_PROJECT" not in source


def test_protected_build_resolves_locally_then_drops_credential_capabilities() -> None:
    publish = _read(".github/workflows/build_and_publish.yml")
    production_deploy = _workflow_job(publish, "deploy")
    staging_deploy = _workflow_job(publish, "deploy-staging")
    build_recipe = _read("just/build.just")
    vite_config = _read("web/vite.config.ts")

    production_resolve = production_deploy[
        production_deploy.index(
            "Resolve production frontend build configuration"
        ) : production_deploy.index("uses: docker/login-action@")
    ]
    staging_resolve = staging_deploy[
        staging_deploy.index(
            "Resolve staging frontend build configuration"
        ) : staging_deploy.index("uses: docker/login-action@")
    ]
    production_build = production_deploy[
        production_deploy.index(
            "Build production images from resolved public configuration"
        ) : production_deploy.index("Remove resolved production frontend configuration")
    ]
    staging_build = staging_deploy[
        staging_deploy.index(
            "Build staging images from resolved public configuration"
        ) : staging_deploy.index("Remove resolved staging frontend configuration")
    ]
    assert "OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN" in production_resolve
    assert "OP_STAGING_SERVICE_ACCOUNT_TOKEN" not in production_resolve
    assert "OP_STAGING_SERVICE_ACCOUNT_TOKEN" in staging_resolve
    assert "OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN" not in staging_resolve
    for resolve_step in (production_resolve, staging_resolve):
        assert "just direnv_export" in resolve_step
        assert "umask 077" in resolve_step
        assert "SENTRY_AUTH_TOKEN" not in resolve_step
        assert "SENTRY_ORG" not in resolve_step
        assert "SENTRY_PROJECT" not in resolve_step
    for build_step in (production_build, staging_build):
        assert "OP_SERVICE_ACCOUNT_TOKEN" not in build_step
        assert "GH_TOKEN:" not in build_step
        assert "JAVASCRIPT_BUILD_ENV_JSON:" in build_step
        assert "${{ secrets." not in build_step
    # common-setup exports the CI/test direnv environment to GITHUB_ENV, so
    # each protected build step must override PYTHON_ENV at step scope.
    assert "PYTHON_ENV: production" in production_build
    assert "PYTHON_ENV: staging" not in production_build
    assert "PYTHON_ENV: staging" in staging_build
    assert "PYTHON_ENV: production" not in staging_build

    assert production_deploy.index("Resolve production frontend") < (
        production_deploy.index("uses: docker/login-action@")
    )
    assert staging_deploy.index("Resolve staging frontend") < staging_deploy.index(
        "uses: docker/login-action@"
    )
    assert production_deploy.count("if: always()") >= 1
    assert staging_deploy.count("if: always()") >= 1
    assert production_deploy.count('rm -f -- "$JAVASCRIPT_BUILD_ENV_JSON"') == 1
    assert staging_deploy.count('rm -f -- "$JAVASCRIPT_BUILD_ENV_JSON"') == 1

    assert (
        'NIXPACKS_CREDENTIAL_BARRIER := "env -u OP_SERVICE_ACCOUNT_TOKEN '
        "-u OP_SOCK -u OP_CONNECT_TOKEN -u GH_TOKEN -u GITHUB_TOKEN "
        '-u SENTRY_AUTH_TOKEN -u SENTRY_ORG -u SENTRY_PROJECT OP_CACHE=false"'
        in build_recipe
    )
    assert 'PYTHON_NIXPACKS_BUILD_CMD := "nixpacks build ."' in build_recipe
    assert 'JAVASCRIPT_NIXPACKS_BUILD_CMD := "nixpacks build "' in build_recipe
    assert "just _javascript_build_env_args" in build_recipe
    assert "just _shared_build_env_args" in build_recipe
    assert 'to_entries[] | "--env\\u0000"' in build_recipe
    assert "xargs -0 {{NIXPACKS_CREDENTIAL_BARRIER}}" in build_recipe
    assert 'if [ -n "${JAVASCRIPT_BUILD_ENV_JSON:-}" ]' in build_recipe
    local_projection = build_recipe[
        build_recipe.index(
            'just direnv_export "{{JAVASCRIPT_SECRETS_FILE}}"'
        ) : build_recipe.index(
            "fi; \\",
            build_recipe.index('just direnv_export "{{JAVASCRIPT_SECRETS_FILE}}"'),
        )
    ]
    assert "jq -ce '{NODE_ENV, OPENAPI_JSON_PATH" in local_projection
    assert "OP_PROD_VAULT_UID" not in local_projection
    assert "OP_STAGING_VAULT_UID" not in local_projection
    assert "Resolved frontend build configuration has an invalid shape." in build_recipe
    assert "GIT_DESCRIPTION" not in build_recipe
    assert "BUILD_DESCRIPTION" not in build_recipe
    assert '" --env CI=true "' in build_recipe
    assert "JAVASCRIPT_PRODUCTION_IMAGE_NAME" not in build_recipe
    assert "docker push {{JAVASCRIPT_IMAGE_TAG}}" not in build_recipe
    assert "docker tag {{JAVASCRIPT_IMAGE_TAG}}" not in build_recipe
    javascript_command = build_recipe[
        build_recipe.index("JAVASCRIPT_NIXPACKS_BUILD_CMD :=") : build_recipe.index(
            "# .env file without any secrets"
        )
    ]
    assert "SENTRY_AUTH_TOKEN" not in javascript_command
    assert "--cache-from" not in javascript_command
    assert "--inline-cache" not in javascript_command
    assert "authToken &&" in vite_config
    assert "sentryVitePlugin" in vite_config
    assert 'if (!CI && !VITE_BUILD_COMMIT.endsWith("-dirty"))' in vite_config

    documentation = _read("infra/README.md")
    assert "Environment-local 1Password prerequisites" in documentation
    assert "`.env.production.backend`" in documentation
    assert "`.env.production.frontend`" in documentation
    assert "`.env.staging.backend`" in documentation
    assert "`.env.staging.frontend`" in documentation
    assert "sentry-runtime/backend-dsn" in documentation
    assert "sentry-runtime/frontend-dsn" in documentation
    assert "assemblyai-runtime/api-key" in documentation
    assert "Source-map release upload stays disabled" in documentation
    assert "`sentry-release` is not a protected-build prerequisite" in documentation


def test_nixpacks_argument_transport_is_safe_for_hostile_metadata(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "shell-expanded"
    hostile = f'$(touch {marker}); `touch {marker}` "quoted" value'

    # A commit subject can contain shell syntax, but the protected build no
    # longer reads or interpolates it into its command at all.
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Security Test",
            "-c",
            "user.email=security-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            hostile,
        ],
        check=True,
    )
    commit_sha = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", commit_sha)
    assert not marker.exists()

    # Public frontend values travel as NUL-delimited argv records into xargs,
    # so shell syntax remains one literal argument and is never evaluated.
    encoded = subprocess.run(
        [
            "jq",
            "-je",
            'to_entries[] | "--env\\u0000", (.key + "=" + .value), "\\u0000"',
        ],
        input=json.dumps({"VITE_POSTHOG_KEY": hostile}).encode(),
        check=True,
        capture_output=True,
    ).stdout
    recorder = tmp_path / "record_args.py"
    recorded = tmp_path / "recorded.json"
    recorder.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["xargs", "-0", sys.executable, str(recorder), str(recorded)],
        input=encoded,
        check=True,
    )
    assert json.loads(recorded.read_text(encoding="utf-8")) == [
        "--env",
        f"VITE_POSTHOG_KEY={hostile}",
    ]
    assert not marker.exists()


def test_production_and_staging_vault_credentials_are_distinct_and_scoped() -> None:
    workflows = "\n".join(
        _read(path)
        for path in (
            ".github/workflows/infra_production_plan.yml",
            ".github/workflows/infra_apply.yml",
            ".github/workflows/build_and_publish.yml",
        )
    )
    production_plan = _workflow_job(
        _read(".github/workflows/infra_production_plan.yml"), "plan"
    )
    production_apply = _workflow_job(
        _read(".github/workflows/infra_apply.yml"), "apply"
    )
    publish = _read(".github/workflows/build_and_publish.yml")
    production_deploy = _workflow_job(publish, "deploy")
    staging_deploy = _workflow_job(publish, "deploy-staging")

    assert "secrets.OP_SERVICE_ACCOUNT_TOKEN" not in workflows
    for production_job in (production_plan, production_apply, production_deploy):
        assert "environment: production" in production_job
        assert "secrets.OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN" in production_job
        assert "secrets.OP_STAGING_SERVICE_ACCOUNT_TOKEN" not in production_job
    assert "environment: staging" in staging_deploy
    assert "secrets.OP_STAGING_SERVICE_ACCOUNT_TOKEN" in staging_deploy
    assert "secrets.OP_PRODUCTION_SERVICE_ACCOUNT_TOKEN" not in staging_deploy
    assert "CLERK_PRIVATE_KEY: ${{ secrets.CLERK_PRIVATE_KEY }}" not in workflows

    for job in (production_plan, production_apply, production_deploy, staging_deploy):
        job_environment = job[: job.index("    steps:\n")]
        assert "OP_SERVICE_ACCOUNT_TOKEN:" not in job_environment
        assert "RECALL_WEBHOOK_SECRET:" not in job_environment
        assert "CLERK_PRIVATE_KEY:" not in job_environment


def test_azure_oidc_trust_is_bound_to_protected_environments() -> None:
    production = _hcl_block(
        _read("infra/main.tf"),
        'resource "azurerm_federated_identity_credential" "ghactions"',
    )
    staging = _hcl_block(
        _read("infra-staging/main.tf"),
        'resource "azurerm_federated_identity_credential" "ghactions"',
    )

    assert 'subject = "repo:CosmaNeura/cosmaneura:environment:production"' in production
    assert 'subject = "repo:CosmaNeura/cosmaneura:environment:staging"' in staging
    assert ":ref:refs/heads/" not in production
    assert ":ref:refs/heads/" not in staging

    workflow = _read(".github/workflows/build_and_publish.yml")
    assert "environment: production" in _workflow_job(workflow, "deploy")
    assert "environment: staging" in _workflow_job(workflow, "deploy-staging")

    documentation = _read("infra/README.md")
    normalized_documentation = " ".join(documentation.split())
    assert "**environment variables**, not repository variables" in documentation
    assert "bound to the production `ghactions` identity" in documentation
    assert "bound to the separate staging `ghactions` identity" in documentation
    assert "OIDC credential reconciliation hold" in documentation
    assert (
        "Do not run a production or staging apply as the bootstrap mechanism"
        in normalized_documentation
    )
    assert (
        "currently has only the stale `ref:refs/heads/master` credential"
        in documentation
    )
    assert "`github-actions-staging-env`" in documentation
    assert "import/adopt" in documentation
    assert "can reject a duplicate issuer/subject pair" in documentation
    assert "both protected deployment paths are on an" in documentation
    assert "operational hold" in documentation
    assert "Never leave a branch-subject" in documentation
    assert "credential as a fallback" in documentation

    assert "cannot bootstrap its own OIDC access" in _read("infra/main.tf")
    assert "can collide with the existing issuer/subject pair" in _read(
        "infra-staging/main.tf"
    )


def test_durable_webhook_rollback_preserves_schema_and_evidence() -> None:
    runbook = _read("docs/RECALL_WEBHOOK_INBOX_RUNBOOK.md")
    normalized_runbook = " ".join(runbook.split())

    for revision in ("dc296f605553", "a81c9d7e4f20", "e4b9a63f210d"):
        assert revision in runbook
    assert "database downgrade is **not** a rollback mechanism" in normalized_runbook
    assert "disable ingress and new capture" in normalized_runbook
    assert "drain in-flight workers without purging queues" in normalized_runbook
    assert "preserve or back up" in normalized_runbook
    assert "source/flag rollback must retain" in normalized_runbook


def test_recall_secret_shape_is_checked_without_value_disclosure() -> None:
    terraform = _read("infra/main.tf")
    config = _read("app/configuration/webhooks.py")
    live_guard = _read("infra/api_only_secret_live_guard.py")
    static_plan = _read("infra/tests/secretless_plan.tftest.hcl")

    assert (
        "recall_webhook_secret == trimspace(local.recall_webhook_secret)" in terraform
    )
    assert 'startswith(local.recall_webhook_secret, "whsec_")' in terraform
    assert "[A-Za-z0-9+/][AQgw]==" in terraform
    assert "[A-Za-z0-9+/]{2}[AEIMQUYcgkosw048]=" in terraform
    assert "recall_webhook_secret_payload) >= 24" in terraform
    assert "recall_webhook_secret_payload) % 4 == 0" in terraform
    assert "validate_recall_webhook_secret" in config
    assert "base64.b64decode(payload, validate=True)" in config
    assert "len(decoded) < _MIN_RECALL_SIGNING_SECRET_BYTES" in config
    assert 'base64.b64encode(decoded).decode("ascii") != payload' in config
    assert "validate_signing_secret" in live_guard
    assert "len(decoded) < MIN_SIGNING_SECRET_BYTES" in live_guard
    assert 'base64.b64encode(decoded).decode("ascii") != payload' in live_guard
    assert 'os.environ.get(ENV_NAME, "")' in live_guard
    assert "whsec_c3RhdGljLXRlc3Qta2V5IQ==" in static_plan
