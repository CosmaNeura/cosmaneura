from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRANT_SECRET = "SOVEREIGN_RECORD_GRANT_SECRET"


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


def _shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}", start)
    return source[start : end + 2]


def test_production_grant_key_is_scoped_only_to_api_container_app() -> None:
    variables = _read("infra/variables.tf")
    common = _hcl_block(variables, 'variable "python_secret_vars"')
    api_only = _hcl_block(variables, 'variable "python_api_only_secret_vars"')

    assert GRANT_SECRET not in common
    assert api_only.count(GRANT_SECRET) == 1

    main = _read("infra/main.tf")
    api = _hcl_block(main, 'resource "azurerm_container_app" "app"')
    assert api.count("var.python_api_only_secret_vars") == 2

    for workload in ("scheduler", "worker"):
        block = _hcl_block(main, f'resource "azurerm_container_app" "{workload}"')
        assert "python_api_only_secret_vars" not in block
        assert GRANT_SECRET not in block

    deploy = _read("infra/deploy.sh")
    assert GRANT_SECRET not in _shell_array(deploy, "COMMON_SECRET_VARS")
    assert GRANT_SECRET not in _shell_function(deploy, "run_migrations")


def test_staging_grant_key_is_scoped_only_to_api_container_app() -> None:
    variables = _read("infra-staging/variables.tf")
    common = _hcl_block(variables, 'variable "python_secret_vars"')
    api_only = _hcl_block(variables, 'variable "python_api_only_secret_vars"')

    assert GRANT_SECRET not in common
    assert api_only.count(GRANT_SECRET) == 1

    deploy = _read("infra-staging/deploy.sh")
    control_default = (
        'ENABLE_SOVEREIGN_RECORD_GRANTS="${ENABLE_SOVEREIGN_RECORD_GRANTS:-false}"'
    )
    assert deploy.index(control_default) < deploy.index("if ! ENV_JSON=")
    assert ".ENABLE_SOVEREIGN_RECORD_GRANTS" not in deploy
    assert GRANT_SECRET not in _shell_array(deploy, "COMMON_SECRET_VARS")
    # Recall's always-configured API-only secret is separate from the optional
    # record-grant capability, which is appended only behind its feature gate.
    assert GRANT_SECRET not in _shell_array(deploy, "API_ONLY_SECRET_VARS")
    assert 'API_CONTAINER="cosmaneura-staging-app"' in deploy
    assert '"$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true"' in deploy
    assert 'container_secret_vars+=("${API_ONLY_SECRET_VARS[@]}")' in deploy
    assert deploy.count("${API_ONLY_SECRET_VARS[@]}") == 1
    assert "container_secret_vars+=(SOVEREIGN_RECORD_GRANT_SECRET)" in deploy
    assert "API_ONLY_SECRET_VARS" not in _shell_function(deploy, "run_migrations")

    deployment = _shell_function(deploy, "initiate_deployment")
    secret_set = deployment.index("if ! az containerapp secret set")
    app_update = deployment.index("\n\taz containerapp update")
    assert secret_set < app_update
    assert 'echo "Failed to configure secrets for $container_name."' in deployment
    assert "return 1" in deployment[secret_set:app_update]

    assert 'remove_env_vars+=("$GRANT_SECRET_ENV_NAME")' in deploy
    assert '--remove-env-vars "${remove_env_vars[@]}"' in deploy
    assert "az containerapp secret remove" in deploy
    assert '--secret-names "$GRANT_SECRET_RESOURCE_NAME"' in deploy

    staging_environment = _read(".env.staging.backend")
    assert (
        'ENABLE_SOVEREIGN_RECORD_GRANTS="${ENABLE_SOVEREIGN_RECORD_GRANTS:-false}"'
        in staging_environment
    )
    guarded_lookup = staging_environment.index(
        'if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true" ]]'
    )
    assert staging_environment.index(GRANT_SECRET) > guarded_lookup

    direnv_recipe = _read("just/direnv.just")
    assert (
        'ENABLE_SOVEREIGN_RECORD_GRANTS="${ENABLE_SOVEREIGN_RECORD_GRANTS:-false}"'
        in direnv_recipe
    )


def test_production_application_requires_manual_plan_and_apply() -> None:
    workflow = _read(".github/workflows/build_and_publish_manual.yml")
    apply_workflow = _read(".github/workflows/infra_apply.yml")
    plan_workflow = _read(".github/workflows/infra_plan.yml")
    production_plan_workflow = _read(".github/workflows/infra_production_plan.yml")

    assert "production_infra_changed" in workflow
    protected_paths = (
        "infra .env .env.production.backend .env.production.frontend .envrc "
        ".tool-versions"
    )
    assert workflow.count(protected_paths) == 2
    assert workflow.count("Justfile just Procfile .github/actions") == 2
    assert workflow.count(".github/workflows/infra_apply.yml") >= 2
    assert workflow.count(".github/workflows/infra_production_plan.yml") >= 2
    assert workflow.count(".github/workflows/build_and_publish_manual.yml") >= 2
    assert "needs: [web, app, deployment-scope, authorize-production]" in workflow
    assert "needs.deployment-scope.outputs.production_infra_changed" not in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "terraform_apply_run_id" in workflow
    assert "provider_signed_recall_callback_verified" in workflow
    assert "Require provider-signed callback gate" in workflow
    assert "real provider-signed non-PHI Recall callback" in workflow
    assert "actions: read" in workflow
    assert "gh api" in workflow
    assert "actions/workflows/infra_apply.yml/runs?" in workflow
    assert '"${apply_run[1]:-}" != ".github/workflows/infra_apply.yml"' in workflow
    assert '"${apply_run[3]:-}" != "workflow_dispatch"' in workflow
    assert "The supplied run is not a successful Terraform Apply" in workflow
    assert 'git merge-base --is-ancestor "$apply_sha" "$TARGET_SHA"' in workflow
    assert 'git diff --quiet "$apply_sha" "$TARGET_SHA" --' in workflow
    assert workflow.count("group: cosmaneura-production-container-apps") == 1
    assert (
        "cancel-in-progress: ${{ github.ref != 'refs/heads/master' }}" not in workflow
    )
    assert workflow.count("cancel-in-progress: false") == 2
    assert apply_workflow.count("group: cosmaneura-production-container-apps") == 1
    assert "cancel-in-progress: false" in apply_workflow
    apply_trigger = apply_workflow[: apply_workflow.index("permissions:\n")]
    assert "  push:" not in apply_trigger
    assert "  workflow_dispatch:" in apply_trigger
    assert "production_plan_run_id:" in apply_trigger
    assert "production_plan_artifact_digest:" in apply_trigger

    assert "      - '.env'" in plan_workflow
    assert "      - '.env.production.backend'" in plan_workflow
    assert "      - '.env.production.frontend'" in plan_workflow
    workflow_scope = plan_workflow[: plan_workflow.index("jobs:\n")]
    assert "permissions:\n  contents: read" in workflow_scope
    assert "issues: write" not in plan_workflow
    assert "pull-requests: write" not in plan_workflow
    assert "Post plan as PR comment" not in plan_workflow
    assert "Terraform PR checks (secretless)" in plan_workflow
    assert "This does not read or diff production state." in plan_workflow
    assert "environment: production" in production_plan_workflow
    assert "group: cosmaneura-production-container-apps" in production_plan_workflow

    documentation = _read("infra/README.md")
    assert "Terraform Production Plan" in documentation
    assert "production_plan_run_id" in documentation
    assert "production_plan_artifact_digest" in documentation
    assert "`terraform_apply_run_id`" in documentation
    assert "`provider_signed_recall_callback_verified`" in documentation
    assert documentation.count("repo:CosmaNeura/cosmaneura:environment:production") >= 3
    assert "repo:CosmaNeura/cosmaneura:environment:staging" in documentation
    assert "staging-scoped Azure identity" in documentation


def test_production_promotion_requires_founder_manual_dispatch() -> None:
    workflow = _read(".github/workflows/build_and_publish_manual.yml")

    authorization_start = workflow.index("\n  authorize-production:")
    staging_authorization_start = workflow.index("\n  authorize-staging:")
    authorization_job = workflow[authorization_start:staging_authorization_start]

    production_start = workflow.index("\n  deploy:")
    staging_start = workflow.index("\n  deploy-staging:")
    production_job = workflow[production_start:staging_start]

    assert "github.event_name == 'workflow_dispatch'" in authorization_job
    assert "github.ref == 'refs/heads/master'" in authorization_job
    assert "permissions: {}" in authorization_job
    assert "PROMOTION_ACTOR: ${{ github.actor }}" in authorization_job
    assert (
        "PROMOTION_TRIGGERING_ACTOR: ${{ github.triggering_actor }}"
        in authorization_job
    )
    assert '"$PROMOTION_REF" != "refs/heads/master"' in authorization_job
    assert '"$PROMOTION_ACTOR" != "JohnClem"' in authorization_job
    assert '"$PROMOTION_TRIGGERING_ACTOR" != "JohnClem"' in authorization_job

    assert "needs: [web, app, deployment-scope, authorize-production]" in production_job
    assert "github.event_name == 'workflow_dispatch'" in production_job
    assert "github.ref == 'refs/heads/master'" in production_job
    assert "github.actor == 'JohnClem'" in production_job
    assert "github.triggering_actor == 'JohnClem'" in production_job
    assert "needs.authorize-production.result == 'success'" in production_job


def test_staging_deployment_requires_founder_manual_dispatch() -> None:
    workflow = _read(".github/workflows/build_and_publish_manual.yml")
    workflow_scope = workflow[: workflow.index("jobs:\n")]

    authorization_start = workflow.index("\n  authorize-staging:")
    deployment_scope_start = workflow.index("\n  deployment-scope:")
    authorization_job = workflow[authorization_start:deployment_scope_start]
    staging_job = workflow[workflow.index("\n  deploy-staging:") :]

    # Pushes still run validation, but they cannot satisfy either deployment job.
    assert "branches: [master, staging]" in workflow_scope
    assert "github.event_name == 'workflow_dispatch'" in authorization_job
    assert "github.ref == 'refs/heads/staging'" in authorization_job
    assert "permissions: {}" in authorization_job
    assert '"$DEPLOYMENT_REF" != "refs/heads/staging"' in authorization_job
    assert '"$DEPLOYMENT_ACTOR" != "JohnClem"' in authorization_job
    assert '"$DEPLOYMENT_TRIGGERING_ACTOR" != "JohnClem"' in authorization_job

    assert "needs: [web, app, authorize-staging]" in staging_job
    assert "github.event_name == 'workflow_dispatch'" in staging_job
    assert "github.ref == 'refs/heads/staging'" in staging_job
    assert "github.actor == 'JohnClem'" in staging_job
    assert "github.triggering_actor == 'JohnClem'" in staging_job
    assert "needs.authorize-staging.result == 'success'" in staging_job
    assert "github.event_name == 'push'" not in staging_job
    assert "environment: staging" in staging_job
    assert staging_job.count("repos/$GITHUB_REPOSITORY/git/ref/heads/staging") == 3
    assert 'current_staging_sha" != "$TARGET_SHA' in staging_job
    early_recheck = staging_job.index(
        "Reverify current staging before binding credentials"
    )
    publish_recheck = staging_job.index(
        "Reverify current staging before publishing images"
    )
    azure_recheck = staging_job.index("Reverify current staging before Azure mutation")
    assert early_recheck < staging_job.index("actions/checkout@")
    assert early_recheck < staging_job.index("OP_STAGING_SERVICE_ACCOUNT_TOKEN")
    assert publish_recheck < staging_job.index("- run: just build_push")
    assert azure_recheck < staging_job.index("name: Deploy to Azure Staging")


def test_production_infrastructure_requires_founder_manual_dispatch() -> None:
    for path, protected_job_name, prefix in (
        (".github/workflows/infra_production_plan.yml", "plan", "PLAN"),
        (".github/workflows/infra_apply.yml", "apply", "APPLY"),
    ):
        workflow = _read(path)
        authorization_start = workflow.index("\n  authorize-production:")
        protected_start = workflow.index(f"\n  {protected_job_name}:")
        authorization_job = workflow[authorization_start:protected_start]
        protected_job = workflow[protected_start:]

        assert "permissions: {}" in authorization_job
        assert "environment: production" not in authorization_job
        assert f"{prefix}_ACTOR: ${{{{ github.actor }}}}" in authorization_job
        assert (
            f"{prefix}_TRIGGERING_ACTOR: ${{{{ github.triggering_actor }}}}"
            in authorization_job
        )
        assert f'"${prefix}_EVENT" != "workflow_dispatch"' in authorization_job
        assert f'"${prefix}_REF" != "refs/heads/master"' in authorization_job
        assert f'"${prefix}_ACTOR" != "JohnClem"' in authorization_job
        assert f'"${prefix}_TRIGGERING_ACTOR" != "JohnClem"' in authorization_job

        assert "needs: authorize-production" in protected_job
        assert "environment: production" in protected_job
        assert "github.event_name == 'workflow_dispatch'" in protected_job
        assert "github.ref == 'refs/heads/master'" in protected_job
        assert "github.actor == 'JohnClem'" in protected_job
        assert "github.triggering_actor == 'JohnClem'" in protected_job
        assert "needs.authorize-production.result == 'success'" in protected_job


def test_application_deploys_cannot_be_cancelled_mid_rollout_and_have_time() -> None:
    workflow = _read(".github/workflows/build_and_publish_manual.yml")
    production_start = workflow.index("\n  deploy:")
    staging_start = workflow.index("\n  deploy-staging:")
    production_job = workflow[production_start:staging_start]
    staging_job = workflow[staging_start:]

    assert "cancel-in-progress: false" in workflow[: workflow.index("jobs:\n")]
    assert "timeout-minutes: 60" in production_job
    assert "timeout-minutes: 60" in staging_job


def test_terraform_plan_installs_only_required_host_package() -> None:
    workflow = _read(".github/workflows/infra_plan.yml")
    assert "./.github/actions/common-setup" not in workflow
    assert "apt-packages:" not in workflow
    assert (
        "opentofu/setup-opentofu@9d84900f3238fab8cd84ce47d658d25dd008be2f" in workflow
    )
    assert "libnss3-tools" not in workflow
    assert "poppler-utils" not in workflow
    assert "ffmpeg" not in workflow
