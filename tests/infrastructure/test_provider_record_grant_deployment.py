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

    for workload in ("scheduler", "worker", "job-monitor"):
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
    assert _shell_array(deploy, "API_ONLY_SECRET_VARS").count(GRANT_SECRET) == 1
    assert 'API_CONTAINER="cosmaneura-staging-app"' in deploy
    assert '"$ENABLE_SOVEREIGN_RECORD_GRANTS" == "true"' in deploy
    assert 'container_secret_vars+=("${API_ONLY_SECRET_VARS[@]}")' in deploy
    assert deploy.count("${API_ONLY_SECRET_VARS[@]}") == 1
    assert "API_ONLY_SECRET_VARS" not in _shell_function(deploy, "run_migrations")

    deployment = _shell_function(deploy, "initiate_deployment")
    secret_set = deployment.index("if ! az containerapp secret set")
    app_update = deployment.index("\n\taz containerapp update")
    assert secret_set < app_update
    assert 'echo "Failed to configure secrets for $container_name."' in deployment
    assert "return 1" in deployment[secret_set:app_update]

    assert 'remove_env_vars+=("$GRANT_SECRET_ENV_NAME")' in deploy
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


def test_infrastructure_push_holds_automatic_application_deploy() -> None:
    workflow = _read(".github/workflows/build_and_publish.yml")
    apply_workflow = _read(".github/workflows/infra_apply.yml")
    plan_workflow = _read(".github/workflows/infra_plan.yml")

    assert "production_infra_changed" in workflow
    protected_paths = (
        "infra .env.production.backend .github/workflows/infra_apply.yml Procfile"
    )
    assert workflow.count(protected_paths) == 2
    assert "needs: [web, app, deployment-scope]" in workflow
    assert (
        "needs.deployment-scope.outputs.production_infra_changed != 'true'" in workflow
    )
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "terraform_apply_run_id" in workflow
    assert "actions: read" in workflow
    assert "gh api" in workflow
    assert "actions/workflows/infra_apply.yml/runs?" in workflow
    assert '"${apply_run[1]:-}" != ".github/workflows/infra_apply.yml"' in workflow
    assert "The supplied run is not a successful Terraform Apply" in workflow
    assert 'git merge-base --is-ancestor "$apply_sha" "$TARGET_SHA"' in workflow
    assert 'git diff --quiet "$apply_sha" "$TARGET_SHA" --' in workflow
    assert workflow.count("group: cosmaneura-production-container-apps") == 1
    assert "cancel-in-progress: ${{ github.ref != 'refs/heads/master' }}" in workflow
    assert workflow.count("cancel-in-progress: false") == 1
    assert apply_workflow.count("group: cosmaneura-production-container-apps") == 1
    assert "cancel-in-progress: false" in apply_workflow
    assert "      - '.env.production.backend'" in apply_workflow

    assert "  issues: write" in plan_workflow
    assert "      - '.env.production.backend'" in plan_workflow
    plan_step = plan_workflow[
        plan_workflow.index("      - name: tofu plan") : plan_workflow.index(
            "      - name: Post plan as PR comment"
        )
    ]
    comment_step = plan_workflow[
        plan_workflow.index("      - name: Post plan as PR comment") :
    ]
    assert "continue-on-error" not in plan_step
    assert "continue-on-error: true" in comment_step

    documentation = _read("infra/README.md")
    assert "Wait for **Terraform Apply**" in documentation
    assert "`terraform_apply_run_id`" in documentation


def test_terraform_plan_installs_only_required_host_package() -> None:
    workflow = _read(".github/workflows/infra_plan.yml")
    setup = workflow[
        workflow.index("      - uses: ./.github/actions/common-setup") : workflow.index(
            "      - uses: azure/login@v3"
        )
    ]

    assert "timeout-minutes: 5" in setup
    assert "        with:" in setup
    assert "          apt-packages: zsh" in setup
    assert "libnss3-tools" not in setup
    assert "poppler-utils" not in setup
    assert "ffmpeg" not in setup
