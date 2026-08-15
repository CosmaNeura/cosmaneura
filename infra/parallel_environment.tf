# This foundation is intentionally empty and false-by-default. It does not clone applications,
# bind custom domains, change DNS, or direct traffic. See docs/PLATFORM_SCRIBE_NETWORK_PLAN.md.
resource "azurerm_virtual_network" "parallel_platform" {
  count = var.deploy_parallel_vnet_environment ? 1 : 0

  name                = "${local.base_name}-parallel-vnet"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.parallel_platform_location
  address_space       = var.parallel_platform_vnet_address_space

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_subnet" "parallel_container_apps" {
  count = var.deploy_parallel_vnet_environment ? 1 : 0

  name                 = "container-apps"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.parallel_platform[0].name
  address_prefixes     = var.parallel_platform_container_apps_subnet_address_prefixes

  delegation {
    name = "Microsoft.App.environments"

    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_log_analytics_workspace" "parallel_platform" {
  count = var.deploy_parallel_vnet_environment ? 1 : 0

  name                = "${local.base_name}-parallel-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.parallel_platform_location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.5

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_container_app_environment" "parallel_platform" {
  count = var.deploy_parallel_vnet_environment ? 1 : 0

  name                = "${local.base_name}-parallel-app-environment"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.parallel_platform_location

  infrastructure_resource_group_name = "${local.base_name}-parallel-managed"
  infrastructure_subnet_id           = azurerm_subnet.parallel_container_apps[0].id
  internal_load_balancer_enabled     = false
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.parallel_platform[0].id
  logs_destination                   = "log-analytics"
  public_network_access              = "Enabled"
  zone_redundancy_enabled            = false

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

# Each VNet owner manages its own peering object. The reverse peering and Scribe private-DNS link
# live in cosmaneura-scribe so the platform state never owns resources inside Scribe's boundary.
resource "azurerm_virtual_network_peering" "parallel_platform_to_scribe" {
  count = var.deploy_parallel_vnet_environment ? 1 : 0

  name                         = "platform-to-scribe"
  resource_group_name          = azurerm_resource_group.main.name
  virtual_network_name         = azurerm_virtual_network.parallel_platform[0].name
  remote_virtual_network_id    = var.scribe_virtual_network_id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = false
  allow_gateway_transit        = false
  use_remote_gateways          = false

  lifecycle {
    precondition {
      condition     = var.scribe_virtual_network_id != ""
      error_message = "scribe_virtual_network_id is required when deploy_parallel_vnet_environment is true"
    }
  }
}
