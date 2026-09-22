locals {
  # Suppress dependent indexes when prerequisites are absent. The activation
  # guard still rejects every partial/one-sided request instead of accepting it.
  private_executor_registry_enabled = (
    var.deploy_staging_restore_control_plane &&
    var.enable_private_executor_network_foundation &&
    var.enable_private_executor_registry
  )
  acrpull_role_definition_id = "${local.subscription_scope}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
}

# An empty, private image store only. Image publication, provenance, private
# pulling and digest-only job configuration remain separate acceptance gates.
resource "azurerm_container_registry" "private_executor" {
  count = local.private_executor_registry_enabled ? 1 : 0

  name                = var.private_executor_registry_name
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  sku                 = "Premium"
  tags                = var.tags

  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = false
  network_rule_bypass_option    = "None"
  data_endpoint_enabled         = true
  export_policy_enabled         = false
  network_rule_set = [{
    default_action = "Deny"
    ip_rule        = []
  }]

  lifecycle {
    # Flags are creation gates, not an infrastructure rollback. An existing
    # registry may contain evidence-bound images even after runtime is paused.
    prevent_destroy = true
  }
}

resource "azurerm_private_dns_zone" "private_executor_registry" {
  count = local.private_executor_registry_enabled ? 1 : 0

  name                = "privatelink.azurecr.io"
  resource_group_name = azurerm_resource_group.control[0].name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "private_executor_registry" {
  count = local.private_executor_registry_enabled ? 1 : 0

  name                  = "cosmaneura-staging-rehearsal-registry-link"
  resource_group_name   = azurerm_resource_group.control[0].name
  private_dns_zone_name = azurerm_private_dns_zone.private_executor_registry[0].name
  virtual_network_id    = azurerm_virtual_network.control[0].id
  registration_enabled  = false
  tags                  = var.tags
}

resource "azurerm_private_endpoint" "private_executor_registry" {
  count = local.private_executor_registry_enabled ? 1 : 0

  name                = "cosmaneura-staging-rehearsal-registry-pe"
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  subnet_id           = azurerm_subnet.private_executor_service_endpoints[0].id
  tags                = var.tags

  private_service_connection {
    name                           = "cosmaneura-staging-rehearsal-registry-connection"
    private_connection_resource_id = azurerm_container_registry.private_executor[0].id
    is_manual_connection           = false
    subresource_names              = ["registry"]
  }

  private_dns_zone_group {
    name                 = "registry"
    private_dns_zone_ids = [azurerm_private_dns_zone.private_executor_registry[0].id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.private_executor_registry]
}

# No dispatcher, publisher, push grant, registry identity or credential. The
# pinned provider does not expose role_assignment_mode: live readback must
# prove classic RBAC mode before AcrPull can count as a working pull binding.
resource "azurerm_role_assignment" "private_executor_acrpull" {
  for_each = local.private_executor_registry_enabled ? local.restore_job_identities : {}

  name                             = uuidv5("url", "${local.subscription_scope}|${var.private_executor_registry_name}|${each.value.name}|acrpull")
  scope                            = azurerm_container_registry.private_executor[0].id
  role_definition_id               = local.acrpull_role_definition_id
  principal_id                     = azurerm_user_assigned_identity.job[each.key].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
