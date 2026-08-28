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


def test_production_database_source_cannot_reintroduce_allow_all() -> None:
    source = _read("infra/main.tf")
    assert (
        'resource "azurerm_postgresql_flexible_server_firewall_rule" "all"'
        not in source
    )
    assert 'name             = "aca-prod-egress"' in source
    assert "start_ip_address = var.production_container_apps_egress_ipv4" in source
    assert "one(azurerm_container_app.scheduler.outbound_ip_addresses)" in source
    assert "azurerm_postgresql_flexible_server_firewall_rule.aca_prod_egress" in source
    assert "var.adopt_existing_production_postgres_egress_rule" in source

    egress_variable = _hcl_block(
        _read("infra/variables.tf"),
        'variable "production_container_apps_egress_ipv4"',
    )
    assert "default" not in egress_variable

    adoption_variable = _hcl_block(
        _read("infra/variables.tf"),
        'variable "adopt_existing_production_postgres_egress_rule"',
    )
    assert "default     = true" in adoption_variable

    for workflow in (
        ".github/workflows/infra_production_plan.yml",
        ".github/workflows/infra_apply.yml",
    ):
        workflow_source = _read(workflow)
        assert "Resolve exact production Container Apps egress" in workflow_source
        assert "cosmaneura-prod-scheduler" in workflow_source
        assert "properties.outboundIpAddresses" in workflow_source
        assert (
            'echo "TF_VAR_production_container_apps_egress_ipv4=$production_egress"'
            in workflow_source
        )


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
    restore = _read("docs/NONPRODUCTION_BACKUP_RESTORE_REHEARSAL.md")
    combined = baseline + incident + restore

    assert "NPI is not required during signup or onboarding" in combined
    assert "NPI is not an authentication" in combined
    assert (
        "Professional credential and licensure status are authorization-neutral"
        in baseline
    )
    assert (
        "Professional credential/licensure status and NPI are authorization-neutral"
        in incident
    )
    assert "continued platform use or real-PHI capabilities" in combined
    assert "CAB is advisory" in combined
    assert "837P" in combined
    assert "EHR write" in combined
    assert "just in time" in combined
    assert "No infrastructure activation gate may be waived" in combined

    for required_gate in (
        "verified identity",
        "valid practice membership and role",
        "owner authority where applicable",
        "current agreements and BAA",
        "active subscription and entitlement",
        "tenant isolation",
        "feature-specific consent",
        "rollout state",
    ):
        assert required_gate in baseline
        assert required_gate in incident

    assert "credential-state" not in baseline
    assert "credential-verification method/reviewer/SLA" not in baseline


def test_public_phi_owner_and_retention_proposals_remain_blocked() -> None:
    baseline = _read("docs/PUBLIC_PHI_INFRASTRUCTURE_BASELINE.md")
    incident = _read("docs/PUBLIC_PHI_INCIDENT_RESPONSE.md")
    restore = _read("docs/NONPRODUCTION_BACKUP_RESTORE_REHEARSAL.md")
    combined = baseline + incident + restore

    assert baseline.count("| John Clemente, CEO | UNASSIGNED |") == 8
    assert incident.count("| John Clemente, CEO | UNASSIGNED |") == 8
    assert "John Clemente, CEO, is the primary" in restore
    assert "Secondary coverage" in baseline
    assert "response targets" in baseline
    assert "contact mechanisms" in baseline

    for retention_rule in (
        "Retain only for the minimum time needed",
        "six-hour hard retry ceiling",
        "never a target holding period",
        "Identifiable raw transcript",
        "14 days",
        "Draft clinical artifacts",
        "30 days",
        "PHI-free operational logs",
        "90 days",
        "Synthetic test data",
        "7 days",
        "legal, billing, and compliance evidence",
        "7 years",
        "Finalized clinical record",
        "counsel",
        "state requirements",
    ):
        assert retention_rule in combined

    assert "The baseline’s proposal is not enforcement evidence" in incident
    assert "Gate 3 remains `BLOCKED`" in baseline
    assert "The gate score is **1 of 16 complete (6%)**" in baseline


def test_incident_advisor_is_design_only_and_non_authoritative() -> None:
    incident = _read("docs/PUBLIC_PHI_INCIDENT_RESPONSE.md")

    assert "Azure Incident Response Advisor" in incident
    assert "internal CosmaNeura design name, not a Microsoft product" in incident
    assert "default-off service" in incident
    assert "advisory-only, read-only, and simulation-only" in incident
    assert "creates no agent, webhook, queue, identity, alert route" in incident
    assert "additional to independent human notification" in incident
    assert "cannot lower severity" in incident
    assert "approve or execute an action" in incident
    assert "earns no activation-gate credit" in incident
