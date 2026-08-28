from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _hcl_block(source: str, declaration: str) -> str:
    start = source.index(declaration)
    opening_brace = source.index("{", start)
    depth = 0

    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise AssertionError(f"unterminated HCL block: {declaration}")


def test_upload_accounts_prohibit_anonymous_blob_publication() -> None:
    for path in ("infra/main.tf", "infra-staging/main.tf"):
        uploads = _hcl_block(
            _read(path), 'resource "azurerm_storage_account" "uploads"'
        )
        assert "allow_nested_items_to_be_public = false" in uploads
        assert 'min_tls_version                 = "TLS1_2"' in uploads
        assert "https_traffic_only_enabled      = true" in uploads


def test_staging_database_source_cannot_reintroduce_allow_all() -> None:
    source = _read("infra-staging/main.tf")
    assert (
        'resource "azurerm_postgresql_flexible_server_firewall_rule" "all"'
        not in source
    )
    assert 'name             = "aca-staging-egress"' in source
    assert "start_ip_address = var.staging_container_apps_egress_ipv4" in source
    assert "one(azurerm_container_app.scheduler.outbound_ip_addresses)" in source
    assert (
        "to = azurerm_postgresql_flexible_server_firewall_rule.aca_staging_egress"
        in source
    )

    egress_variable = _hcl_block(
        _read("infra-staging/variables.tf"),
        'variable "staging_container_apps_egress_ipv4"',
    )
    assert "default" not in egress_variable


def test_observability_is_default_off_and_aggregate_only() -> None:
    for path in ("infra/variables.tf", "infra-staging/variables.tf"):
        source = _read(path)
        marker = 'variable "enable_public_phi_observability"'
        section = source[source.index(marker) :]
        assert "default     = false" in section[:500]

    module = _read("infra/modules/public_phi_observability/main.tf")
    assert module.count("| summarize failure_count = count()") == 4
    assert "enabled_log {" not in module
    assert module.count('resource "azurerm_monitor_diagnostic_setting"') == 3
    assert (
        module.count('resource "azurerm_monitor_scheduled_query_rules_alert_v2"') == 1
    )
    assert module.count('resource "azurerm_monitor_metric_alert"') == 1
    assert module.count('resource "azurerm_monitor_activity_log_alert"') == 1


def test_startup_migration_switch_is_not_propagated_before_identity_rung() -> None:
    assert "AUTO_MIGRATE_ON_STARTUP" in _read("app/configuration/database.py")

    for path in (
        ".env.production.backend",
        ".env.staging.backend",
        "infra/variables.tf",
        "infra-staging/variables.tf",
        "infra-staging/deploy.sh",
    ):
        assert "AUTO_MIGRATE_ON_STARTUP" not in _read(path)


def test_owner_policy_is_an_activation_control_not_an_auth_substitute() -> None:
    baseline = _read("docs/PUBLIC_PHI_INFRASTRUCTURE_BASELINE.md")
    incident = _read("docs/PUBLIC_PHI_INCIDENT_RESPONSE.md")
    combined = baseline + incident

    assert "NPI is not required during signup or onboarding" in combined
    assert "NPI is not an authentication" in combined
    assert "CAB is advisory" in combined
    assert "837P" in combined
    assert "EHR write" in combined
    assert "just in time" in combined
    assert "No infrastructure activation gate may be waived" in combined
