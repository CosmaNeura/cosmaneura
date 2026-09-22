locals {
  private_launch_mailbox_storage_enabled = (
    var.deploy_staging_restore_control_plane &&
    var.enable_private_executor_network_foundation &&
    var.enable_private_launch_mailbox_storage
  )
  private_launch_mailbox_containers = {
    provision = "restore-provision-launches-v1"
    cleanup   = "restore-cleanup-launches-v1"
  }
}

# ARM-only management exports no keys or connection strings. This distinct
# mailbox account is empty until separately reviewed broker/job bindings succeed.
resource "azapi_resource" "private_launch_mailbox_account" {
  count = local.private_launch_mailbox_storage_enabled ? 1 : 0

  type      = "Microsoft.Storage/storageAccounts@2023-05-01"
  name      = var.private_launch_mailbox_storage_account_name
  parent_id = azurerm_resource_group.control[0].id
  location  = azurerm_resource_group.control[0].location
  tags      = var.tags

  body = {
    kind = "StorageV2"
    sku = {
      name = "Standard_ZRS"
    }
    properties = {
      accessTier                   = "Hot"
      dnsEndpointType              = "Standard"
      supportsHttpsTrafficOnly     = true
      minimumTlsVersion            = "TLS1_2"
      allowBlobPublicAccess        = false
      allowSharedKeyAccess         = false
      defaultToOAuthAuthentication = true
      allowCrossTenantReplication  = false
      publicNetworkAccess          = "Disabled"
      isHnsEnabled                 = false
      isNfsV3Enabled               = false
      isSftpEnabled                = false
      isLocalUserEnabled           = false
      encryption = {
        keySource                       = "Microsoft.Storage"
        requireInfrastructureEncryption = true
        services = {
          blob = { enabled = true, keyType = "Account" }
          file = { enabled = true, keyType = "Account" }
        }
      }
      networkAcls = {
        bypass              = "None"
        defaultAction       = "Deny"
        ipRules             = []
        virtualNetworkRules = []
        resourceAccessRules = []
      }
    }
  }

  schema_validation_enabled = true
  response_export_values    = ["properties.primaryEndpoints.blob"]

  lifecycle {
    prevent_destroy = true
  }
}

# Storage creates the default service with the account. Update only its exact
# versioning property through ARM; no data-plane read or synthetic import.
# Removing this resource does not undo versioning or delete existing versions.
resource "azapi_update_resource" "private_launch_mailbox_blob_versioning" {
  count = local.private_launch_mailbox_storage_enabled ? 1 : 0

  type        = "Microsoft.Storage/storageAccounts/blobServices@2023-05-01"
  resource_id = "${azapi_resource.private_launch_mailbox_account[0].id}/blobServices/default"
  body = {
    properties = {
      isVersioningEnabled = true
    }
  }
  response_export_values = []
}

resource "azapi_resource" "private_launch_mailbox_container" {
  for_each = local.private_launch_mailbox_storage_enabled ? local.private_launch_mailbox_containers : {}

  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  name      = each.value
  parent_id = "${azapi_resource.private_launch_mailbox_account[0].id}/blobServices/default"
  body = {
    properties = {
      publicAccess = "None"
    }
  }

  schema_validation_enabled = true
  response_export_values    = []

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [azapi_update_resource.private_launch_mailbox_blob_versioning]
}

resource "azurerm_private_endpoint" "private_launch_mailbox" {
  count = local.private_launch_mailbox_storage_enabled ? 1 : 0

  name                = "cosmaneura-staging-rehearsal-launch-mailbox-pe"
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  subnet_id           = azurerm_subnet.private_executor_service_endpoints[0].id
  tags                = var.tags

  private_service_connection {
    name                           = "cosmaneura-staging-rehearsal-launch-mailbox-connection"
    private_connection_resource_id = azapi_resource.private_launch_mailbox_account[0].id
    is_manual_connection           = false
    subresource_names              = ["blob"]
  }

  private_dns_zone_group {
    name                 = "blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.marker_blob[0].id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.marker_blob]
}
