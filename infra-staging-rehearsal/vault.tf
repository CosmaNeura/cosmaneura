locals {
  private_executor_vault_enabled = (
    var.deploy_staging_restore_control_plane &&
    var.enable_private_executor_network_foundation &&
    var.enable_private_executor_vault
  )
}

# ARM-only: AzureRM 4.73.0 still probes certificate contacts during vault reads.
# No secret/certificate/key value, implicit recovery or data-plane call belongs
# in this root. Exact-version secret publication and read grants are later work.
resource "azapi_resource" "private_executor_vault" {
  count = local.private_executor_vault_enabled ? 1 : 0

  type      = "Microsoft.KeyVault/vaults@2023-07-01"
  name      = var.private_executor_vault_name
  parent_id = azurerm_resource_group.control[0].id
  location  = azurerm_resource_group.control[0].location
  tags      = var.tags

  body = {
    properties = {
      tenantId = var.tenant_id
      sku = {
        family = "A"
        name   = "standard"
      }
      createMode                   = "default"
      accessPolicies               = []
      enableRbacAuthorization      = true
      enabledForDeployment         = false
      enabledForDiskEncryption     = false
      enabledForTemplateDeployment = false
      publicNetworkAccess          = "Disabled"
      enableSoftDelete             = true
      softDeleteRetentionInDays    = 90
      enablePurgeProtection        = true
      networkAcls = {
        bypass              = "None"
        defaultAction       = "Deny"
        ipRules             = []
        virtualNetworkRules = []
      }
    }
  }

  schema_validation_enabled = true
  response_export_values    = ["properties.vaultUri"]

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_private_dns_zone" "private_executor_vault" {
  count = local.private_executor_vault_enabled ? 1 : 0

  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = azurerm_resource_group.control[0].name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "private_executor_vault" {
  count = local.private_executor_vault_enabled ? 1 : 0

  name                  = "cosmaneura-staging-rehearsal-vault-link"
  resource_group_name   = azurerm_resource_group.control[0].name
  private_dns_zone_name = azurerm_private_dns_zone.private_executor_vault[0].name
  virtual_network_id    = azurerm_virtual_network.control[0].id
  registration_enabled  = false
  tags                  = var.tags
}

resource "azurerm_private_endpoint" "private_executor_vault" {
  count = local.private_executor_vault_enabled ? 1 : 0

  name                = "cosmaneura-staging-rehearsal-vault-pe"
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  subnet_id           = azurerm_subnet.private_executor_service_endpoints[0].id
  tags                = var.tags

  private_service_connection {
    name                           = "cosmaneura-staging-rehearsal-vault-connection"
    private_connection_resource_id = azapi_resource.private_executor_vault[0].id
    is_manual_connection           = false
    subresource_names              = ["vault"]
  }

  private_dns_zone_group {
    name                 = "vault"
    private_dns_zone_ids = [azurerm_private_dns_zone.private_executor_vault[0].id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.private_executor_vault]
}
