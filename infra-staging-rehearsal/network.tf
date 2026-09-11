# Persistent marker-plane network only. The restore controller continues to
# create one ephemeral, unpeered 10.75.0.0/24 target VNet inside the otherwise
# empty target RG. This distinct VNet does not make that target reachable.
resource "azurerm_virtual_network" "control" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                = "cosmaneura-staging-rehearsal-control-vnet"
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  address_space       = [local.control_vnet_address_space]
  dns_servers         = []
  tags                = var.tags
}

resource "azurerm_subnet" "marker_private_endpoint" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                 = "marker-private-endpoints"
  resource_group_name  = azurerm_resource_group.control[0].name
  virtual_network_name = azurerm_virtual_network.control[0].name
  address_prefixes     = [local.marker_private_endpoint_subnet]

  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = false
}

# This subnet remains an inert reservation in the base control-plane plan. The
# independently approved network-foundation plan adds only the exact Container
# Apps environment delegation. It does not create an environment, workload,
# ingress path, NAT, route table, peering, or NSG.
resource "azurerm_subnet" "private_executor_reserved" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                 = "private-executor-reserved"
  resource_group_name  = azurerm_resource_group.control[0].name
  virtual_network_name = azurerm_virtual_network.control[0].name
  address_prefixes     = [local.private_executor_infrastructure_subnet]

  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Enabled"
  private_link_service_network_policies_enabled = true

  dynamic "delegation" {
    for_each = var.enable_private_executor_network_foundation ? [true] : []

    content {
      name = "private-executor-container-apps"

      service_delegation {
        name = "Microsoft.App/environments"
        actions = [
          "Microsoft.Network/virtualNetworks/subnets/join/action",
        ]
      }
    }
  }
}

# Reserved for ACR, Key Vault, private evidence/runtime-record Blob, private
# launch-mailbox Blob, and per-rehearsal PostgreSQL private endpoints. No endpoint
# or DNS zone is created by this network-only rung.
resource "azurerm_subnet" "private_executor_service_endpoints" {
  count = (
    var.deploy_staging_restore_control_plane &&
    var.enable_private_executor_network_foundation
  ) ? 1 : 0

  name                 = "private-executor-service-endpoints"
  resource_group_name  = azurerm_resource_group.control[0].name
  virtual_network_name = azurerm_virtual_network.control[0].name
  address_prefixes     = [local.private_executor_service_endpoint_subnet]

  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

resource "azurerm_private_dns_zone" "marker_blob" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.control[0].name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "marker_blob" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                  = "cosmaneura-staging-rehearsal-marker-link"
  resource_group_name   = azurerm_resource_group.control[0].name
  private_dns_zone_name = azurerm_private_dns_zone.marker_blob[0].name
  virtual_network_id    = azurerm_virtual_network.control[0].id
  registration_enabled  = false
  tags                  = var.tags
}
