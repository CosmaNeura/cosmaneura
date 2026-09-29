"""Deployment ownership for Stripe's acquisition and maintenance capabilities."""

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


def test_production_scopes_stripe_capabilities_to_api_and_worker() -> None:
    variables = _read("infra/variables.tf")
    common = _hcl_block(variables, 'variable "python_secret_vars"')
    billing = _hcl_block(variables, 'variable "python_billing_secret_vars"')
    api_only = _hcl_block(variables, 'variable "python_billing_api_only_secret_vars"')
    assert "STRIPE_SECRET_KEY" not in common
    assert billing.count("STRIPE_SECRET_KEY") == 1
    assert api_only.count("STRIPE_WEBHOOK_SECRET") == 1

    main = _read("infra/main.tf")
    api = _hcl_block(main, 'resource "azurerm_container_app" "app"')
    worker = _hcl_block(main, 'resource "azurerm_container_app" "worker"')
    assert api.count("var.python_billing_secret_vars") == 2
    assert api.count("var.python_billing_api_only_secret_vars") == 2
    assert worker.count("var.python_billing_secret_vars") == 2
    assert "python_billing_api_only_secret_vars" not in worker
    for workload in ("scheduler",):
        block = _hcl_block(main, f'resource "azurerm_container_app" "{workload}"')
        assert "python_billing" not in block

    migrations = _shell_function(_read("infra/deploy.sh"), "run_migrations")
    assert "STRIPE_SECRET_KEY" not in migrations
    assert "STRIPE_WEBHOOK_SECRET" not in migrations
    assert (
        'lower(try(data.external.secrets.result["STRIPE_LIVE_MODE"], "")) == "true"'
        in main
    )
    assert (
        'startswith(trimspace(try(data.external.secrets.result["STRIPE_SECRET_KEY"], "")), "sk_live_")'
        in main
    )
    assert (
        'startswith(trimspace(try(data.external.secrets.result["STRIPE_SECRET_KEY"], "")), "rk_live_")'
        in main
    )


def test_backend_envs_are_default_off_and_resolve_no_guessed_coordinate() -> None:
    for path in (".env.production.backend", ".env.staging.backend"):
        source = _read(path)
        assert "export STRIPE_INTEGRATION_ENABLED=false" in source
        assert "export STRIPE_BILLING_ENABLED=false" in source
        assert "export STRIPE_API_VERSION=2025-09-30.clover" in source
        assert "export STRIPE_BILLING_ITEM_ID=" in source
        for name in (
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "STRIPE_ACCOUNT_ID",
            "STRIPE_PRICE_ID",
            "STRIPE_API_VERSION",
        ):
            assert f"export {name}=" in source
        assert "${STRIPE_BILLING_ITEM_ID}" in source
        assert "STRIPE_BILLING_ITEM_ID is required" in source


def test_staging_deploy_owns_and_revokes_exact_stripe_scope() -> None:
    deploy = _read("infra-staging/deploy.sh")
    common = _shell_array(deploy, "COMMON_SECRET_VARS")
    assert "STRIPE_SECRET_KEY" not in common
    assert "STRIPE_WEBHOOK_SECRET" not in common
    assert _shell_array(deploy, "BILLING_SECRET_VARS").count("STRIPE_SECRET_KEY") == 1
    assert (
        _shell_array(deploy, "BILLING_API_ONLY_SECRET_VARS").count(
            "STRIPE_WEBHOOK_SECRET"
        )
        == 1
    )
    assert 'WORKER_CONTAINER="cosmaneura-staging-worker"' in deploy
    assert 'billing_workload_for "$container_name"' in deploy
    assert 'container_secret_vars+=("${BILLING_SECRET_VARS[@]}")' in deploy
    assert 'container_secret_vars+=("${BILLING_API_ONLY_SECRET_VARS[@]}")' in deploy
    assert "STRIPE_SECRET_KEY" not in _shell_function(deploy, "run_migrations")
    assert "STRIPE_WEBHOOK_SECRET" not in _shell_function(deploy, "run_migrations")
    assert "--remove-env-vars" in deploy
    assert "remove_unowned_billing_secrets" in deploy
    assert "stripe-secret-key" in deploy
    assert "stripe-webhook-secret" in deploy
    assert "STAGING_WORKER_COMMAND=" in deploy
    assert "SCHEDULER_COMMAND=" in deploy
    assert "--beat --scheduler redbeat.RedBeatScheduler" not in deploy
    assert 'update_args+=(--args "$STAGING_WORKER_COMMAND")' in deploy
    assert 'update_args+=(--args "$SCHEDULER_COMMAND")' in deploy
    assert "Staging Stripe mode must remain explicitly false" in deploy
    assert "rk_test_" in deploy
    forced_false = next(
        line.split('"')[1].split()
        for line in _shell_function(deploy, "build_configuration_evidence").splitlines()
        if line.startswith("forced_false =")
    )
    assert "STRIPE_LIVE_MODE" in forced_false
    assert "STRIPE_INTEGRATION_ENABLED" not in forced_false
    assert "STRIPE_BILLING_ENABLED" not in forced_false


def test_staging_stripe_flags_are_never_inherited_from_the_operator_shell() -> None:
    # direnv exports only values that differ from its environment. An inherited
    # Stripe flag equal to the reviewed value would drop out of the export and
    # the deploy would fall back to false.
    recipes = _read("just/direnv.just")
    start = recipes.index("@direnv_export target")
    recipe = recipes[start : recipes.index("\n@", start + 1)]
    assert "env -i " in recipe
    assert "STRIPE_" not in recipe


def test_portal_configuration_is_resolved_only_with_the_integration() -> None:
    for path, vault in (
        (".env.production.backend", "OP_PROD_VAULT_UID"),
        (".env.staging.backend", "OP_STAGING_VAULT_UID"),
    ):
        source = _read(path)
        assert source.count("export STRIPE_PORTAL_CONFIGURATION_ID=\n") == 1
        integration = source[source.index("STRIPE_BILLING_ITEM_ID is required") :]
        integration = integration[integration.index("op_inject_source <<'EOF'") :]
        integration = integration[: integration.index("\nEOF\n")]
        assert (
            "export STRIPE_PORTAL_CONFIGURATION_ID="
            f"'op://${{{vault}}}/${{STRIPE_BILLING_ITEM_ID}}/portal-configuration-id'"
        ) in integration


def test_portal_configuration_is_scoped_like_other_billing_settings() -> None:
    for path in ("infra/variables.tf", "infra-staging/variables.tf"):
        variables = _read(path)
        billing = _hcl_block(variables, 'variable "python_billing_public_vars"')
        public = _hcl_block(variables, 'variable "python_public_vars"')
        assert billing.count('"STRIPE_PORTAL_CONFIGURATION_ID"') == 1
        assert "STRIPE_PORTAL_CONFIGURATION_ID" not in public

    deploy = _read("infra-staging/deploy.sh")
    assert (
        _shell_array(deploy, "BILLING_PUBLIC_VARS").count(
            "STRIPE_PORTAL_CONFIGURATION_ID"
        )
        == 1
    )
    public = deploy[deploy.index("\nPUBLIC_VARS=(") :]
    assert "STRIPE_PORTAL_CONFIGURATION_ID" not in public[: public.index("\n)")]


def test_integration_requires_pinned_portal_configuration_shape() -> None:
    main = _read("infra/main.tf")
    assert (
        'startswith(trimspace(try(data.external.secrets.result["STRIPE_PORTAL_CONFIGURATION_ID"], "")), "bpc_")'
        in main
    )

    deploy = _read("infra-staging/deploy.sh")
    assert '"STRIPE_PORTAL_CONFIGURATION_ID": ("bpc_",),' in deploy
    required = deploy[deploy.index("\tfor var in STRIPE_SECRET_KEY") :]
    assert "STRIPE_PORTAL_CONFIGURATION_ID" in required[: required.index("; do")]
    assert '"STRIPE_PORTAL_CONFIGURATION_ID:bpc_"' in deploy
