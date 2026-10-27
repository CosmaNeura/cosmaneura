from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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


def test_open_tofu_owns_both_database_secrets_and_selected_reference() -> None:
    main = _read("infra/main.tf")

    assert '"database-url-v2" = local.database_url_v2_value' in main
    for resource in ("app", "scheduler", "worker"):
        block = _hcl_block(main, f'resource "azurerm_container_app" "{resource}"')
        # Keep DATABASE_URL in the legacy set-driven blocks. Moving it to a
        # static block rotates AzureRM's ordered env list and creates three
        # otherwise-no-op production revisions before the cutover window.
        assert 'toset(["DATABASE_URL"])' not in block
        assert 'name        = "DATABASE_URL"' not in block
        assert (
            block.count('env.key == "DATABASE_URL" ? local.database_url_secret_name')
            == 1
        )
        assert "for_each = local.database_url_v2_secret_values" in block
        assert "value = data.external.secrets.result[secret.key]" in block
        assert "condition     = local.database_url_live_guard_valid" in block
        assert "template[0].container[0].image" in block

        if resource == "app":
            assert "var.python_secret_vars," in block
            assert "var.python_api_only_secret_vars," in block
        elif resource == "worker":
            # Billing maintenance adds one conditional secret set to the
            # worker's two legacy DATABASE_URL-bearing blocks. Both still
            # include the complete common secret set.
            assert block.count("var.python_secret_vars,") == 2
            assert block.count("var.python_billing_secret_vars") == 2
        else:
            assert block.count("for_each = var.python_secret_vars") == 2


def test_live_guard_makes_selector_adoption_only() -> None:
    variables = _read("infra/variables.tf")
    guard = _hcl_block(variables, 'data "external" "database_url_live_guard"')

    assert 'program = ["python", "${path.module}/database_url_live_guard.py"]' in guard
    assert "selected_secret = local.database_url_secret_name" in guard
    assert "v2_enabled      = local.database_url_v2_enabled_text" in guard

    environment = _read(".env.production.backend")
    assert "export DATABASE_URL_V2_ENABLED=false" in environment
    assert "export DATABASE_URL_SECRET_NAME=database-url" in environment
    guard = environment.index('if [ "$DATABASE_URL_V2_ENABLED" = true ]')
    lookup = environment.index("postgres-app-v2/python-connection-string")
    assert guard < lookup


def test_terraform_plan_uses_only_required_host_package() -> None:
    workflow = _read(".github/workflows/infra_production_plan_local.yml")
    setup = workflow[
        workflow.index("      - uses: ./.github/actions/common-setup") : workflow.index(
            "      - uses: azure/login@"
        )
    ]

    assert "timeout-minutes: 5" in setup
    assert "        with:" in setup
    assert "          apt-packages: zsh" in setup
    assert "libnss3-tools" not in setup
    assert "poppler-utils" not in setup
    assert "ffmpeg" not in setup


def test_deploy_adopts_selection_and_never_recreates_migration_job() -> None:
    deploy = _read("infra/deploy.sh")
    migrations = _shell_function(deploy, "run_migrations")

    assert "DATABASE_URL" not in _shell_array(deploy, "COMMON_SECRET_VARS")
    assert deploy.index("database_url_live_guard.py") < deploy.index("run_migrations()")
    live_guard = deploy[deploy.index("if ! jq -n") : deploy.index("COMMON_SECRET_VARS")]
    assert "database_url_live_guard.py" in live_guard
    assert "exit 1" in live_guard
    assert "containerapp job show" in migrations
    assert (
        "Migration job is missing; provision and review it before deploying."
        in migrations
    )
    assert "containerapp job create" not in deploy
    assert "containerapp job delete" not in deploy
    assert "--reconcile-migration-secrets-stdin" in migrations
    assert '--selected-secret-name "$DATABASE_SECRET_NAME"' in migrations
    assert '"database-url": .DATABASE_URL' not in migrations
    assert '"database-url-v2": .DATABASE_URL_V2' in migrations

    workflow = _read(".github/workflows/build_and_publish_local.yml")
    production_deploy = workflow[workflow.index("  deploy:\n") :]
    assert "AZURE_SUBSCRIPTION_ID:" in production_deploy
    assert "vars.AZURE_SUBSCRIPTION_ID || secrets.AZURE_SUBSCRIPTION_ID" in (
        production_deploy
    )
