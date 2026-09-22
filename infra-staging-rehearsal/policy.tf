locals {
  deny_postgres_firewall_rule = {
    if = {
      field  = "type"
      equals = "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules"
    }
    then = {
      effect = "deny"
    }
  }
}

resource "azurerm_policy_definition" "deny_postgres_firewall_rules" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name         = "deny-staging-restore-firewall-rules"
  policy_type  = "Custom"
  mode         = "All"
  display_name = "Deny PostgreSQL firewall rules in the staging restore target"
  description  = "Fail closed if any restore path attempts to create a public PostgreSQL firewall rule."
  policy_rule  = jsonencode(local.deny_postgres_firewall_rule)
  metadata = jsonencode({
    category = "Cosmaneura staging restore"
    version  = "1.0.0"
  })

  lifecycle {
    precondition {
      condition     = sha256(jsonencode(local.deny_postgres_firewall_rule)) != ""
      error_message = "The canonical deny rule must have a stable digest"
    }
  }
}

resource "azurerm_resource_group_policy_assignment" "deny_postgres_firewall_rules" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                 = "deny-staging-restore-firewall-rules"
  resource_group_id    = azurerm_resource_group.target[0].id
  policy_definition_id = azurerm_policy_definition.deny_postgres_firewall_rules[0].id
  enforce              = true
  description          = "Deny every PostgreSQL Flexible Server firewall-rule resource in the ephemeral restore target RG."
}
