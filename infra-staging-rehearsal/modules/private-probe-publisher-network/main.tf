terraform {
  required_version = "= 1.12.5"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "= 4.73.0" }
    azapi   = { source = "Azure/azapi", version = "= 2.4.0" }
  }
}

locals {
  # Suppress partial resource bindings; the independent guard still refuses
  # missing/one-sided approvals and inputs instead of accepting a partial plan.
  active = (
    var.enabled && var.exact_network_plan_approved &&
    var.github_organization_database_id != "" && var.registry_resource_id != "" &&
    length(var.reviewed_https_domains) > 0
  )
  location = "eastus"
  subnets = {
    firewall   = { name = "AzureFirewallSubnet", cidr = "10.77.0.0/26" }
    management = { name = "AzureFirewallManagementSubnet", cidr = "10.77.0.64/26" }
    runner     = { name = "github-probe-publisher", cidr = "10.77.0.128/27" }
    registry   = { name = "probe-registry-private-endpoints", cidr = "10.77.0.160/27" }
  }
}

resource "terraform_data" "guard" {
  input = { enabled = var.enabled, approved = var.exact_network_plan_approved }
  lifecycle {
    precondition {
      condition     = var.enabled == var.exact_network_plan_approved
      error_message = "Network creation and exact saved-plan approval must be enabled or disabled together"
    }
    precondition {
      condition = !var.enabled || (
        var.github_organization_database_id != "" && var.registry_resource_id != "" &&
        length(var.reviewed_https_domains) > 0
      )
      error_message = "An enabled publisher network requires exact organization, registry and reviewed outbound domains"
    }
  }
}

# Separate and unpeered: no existing executor/endpoint subnet is reused and no
# route to vault, database, mailbox or evidence networks is introduced.
resource "azurerm_resource_group" "publisher" {
  count    = local.active ? 1 : 0
  name     = "cosmaneura-staging-probe-publisher"
  location = local.location
  tags     = var.tags
  lifecycle { prevent_destroy = true }
  depends_on = [terraform_data.guard]
}

resource "azurerm_virtual_network" "publisher" {
  count               = local.active ? 1 : 0
  name                = "cosmaneura-staging-probe-publisher-vnet"
  resource_group_name = azurerm_resource_group.publisher[0].name
  location            = local.location
  address_space       = ["10.77.0.0/24"]
  dns_servers         = []
  tags                = var.tags
  lifecycle { prevent_destroy = true }
}

# Four explicit roles keep delegation and firewall-only subnet bindings distinct.
resource "azurerm_subnet" "firewall" {
  count                                         = local.active ? 1 : 0
  name                                          = local.subnets.firewall.name
  resource_group_name                           = azurerm_resource_group.publisher[0].name
  virtual_network_name                          = azurerm_virtual_network.publisher[0].name
  address_prefixes                              = [local.subnets.firewall.cidr]
  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Enabled"
  private_link_service_network_policies_enabled = true
}

resource "azurerm_subnet" "management" {
  count                                         = local.active ? 1 : 0
  name                                          = local.subnets.management.name
  resource_group_name                           = azurerm_resource_group.publisher[0].name
  virtual_network_name                          = azurerm_virtual_network.publisher[0].name
  address_prefixes                              = [local.subnets.management.cidr]
  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Enabled"
  private_link_service_network_policies_enabled = true
}

resource "azurerm_subnet" "runner" {
  count                                         = local.active ? 1 : 0
  name                                          = local.subnets.runner.name
  resource_group_name                           = azurerm_resource_group.publisher[0].name
  virtual_network_name                          = azurerm_virtual_network.publisher[0].name
  address_prefixes                              = [local.subnets.runner.cidr]
  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Enabled"
  private_link_service_network_policies_enabled = true
  delegation {
    name = "github-hosted-publisher"
    service_delegation {
      name    = "GitHub.Network/networkSettings"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "registry" {
  count                                         = local.active ? 1 : 0
  name                                          = local.subnets.registry.name
  resource_group_name                           = azurerm_resource_group.publisher[0].name
  virtual_network_name                          = azurerm_virtual_network.publisher[0].name
  address_prefixes                              = [local.subnets.registry.cidr]
  default_outbound_access_enabled               = false
  private_endpoint_network_policies             = "Disabled"
  private_link_service_network_policies_enabled = true
}

resource "azurerm_network_security_group" "runner" {
  count               = local.active ? 1 : 0
  name                = "probe-publisher-runner-nsg"
  resource_group_name = azurerm_resource_group.publisher[0].name
  location            = local.location
  tags                = var.tags
}

locals {
  runner_rules = {
    deny_inbound = { priority = 100, direction = "Inbound", access = "Deny", protocol = "*", port = "*", destination = "*" }
    registry     = { priority = 100, direction = "Outbound", access = "Allow", protocol = "Tcp", port = "443", destination = local.subnets.registry.cidr }
    dns_udp      = { priority = 110, direction = "Outbound", access = "Allow", protocol = "Udp", port = "53", destination = "AzurePlatformDNS" }
    dns_tcp      = { priority = 111, direction = "Outbound", access = "Allow", protocol = "Tcp", port = "53", destination = "AzurePlatformDNS" }
    https_routed = { priority = 120, direction = "Outbound", access = "Allow", protocol = "Tcp", port = "443", destination = "Internet" }
    deny_other   = { priority = 4000, direction = "Outbound", access = "Deny", protocol = "*", port = "*", destination = "*" }
  }
}

resource "azurerm_network_security_rule" "runner" {
  for_each                    = local.active ? local.runner_rules : {}
  name                        = each.key
  resource_group_name         = azurerm_resource_group.publisher[0].name
  network_security_group_name = azurerm_network_security_group.runner[0].name
  priority                    = each.value.priority
  direction                   = each.value.direction
  access                      = each.value.access
  protocol                    = each.value.protocol
  source_port_range           = "*"
  destination_port_range      = each.value.port
  source_address_prefix       = each.value.direction == "Inbound" ? "*" : local.subnets.runner.cidr
  destination_address_prefix  = each.value.destination
}

resource "azurerm_subnet_network_security_group_association" "runner" {
  count                     = local.active ? 1 : 0
  subnet_id                 = azurerm_subnet.runner[0].id
  network_security_group_id = azurerm_network_security_group.runner[0].id
  depends_on                = [azurerm_network_security_rule.runner]
}

# Public IPs belong only to the managed firewall's outbound/control plane.
# They do not enable registry public access, inbound runner access or DNAT.
resource "azurerm_public_ip" "firewall" {
  for_each            = local.active ? toset(["data", "management"]) : toset([])
  name                = "probe-publisher-firewall-${each.key}"
  resource_group_name = azurerm_resource_group.publisher[0].name
  location            = local.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_firewall_policy" "publisher" {
  count               = local.active ? 1 : 0
  name                = "probe-publisher-egress-policy"
  resource_group_name = azurerm_resource_group.publisher[0].name
  location            = local.location
  sku                 = "Basic"
  # Basic supports threat-intelligence alerts only. Explicit application rules
  # enforce the reviewed HTTPS allowlist; do not claim threat-feed blocking.
  threat_intelligence_mode = "Alert"
  tags                     = var.tags
}

resource "azurerm_firewall_policy_rule_collection_group" "egress" {
  count              = local.active ? 1 : 0
  name               = "reviewed-publisher-https"
  firewall_policy_id = azurerm_firewall_policy.publisher[0].id
  priority           = 100
  application_rule_collection {
    name     = "exact-reviewed-https-domains"
    priority = 100
    action   = "Allow"
    rule {
      name              = "publisher-control-plane"
      source_addresses  = [local.subnets.runner.cidr]
      destination_fqdns = sort(tolist(var.reviewed_https_domains))
      protocols {
        type = "Https"
        port = 443
      }
    }
  }
}

resource "azurerm_firewall" "publisher" {
  count               = local.active ? 1 : 0
  name                = "probe-publisher-firewall"
  resource_group_name = azurerm_resource_group.publisher[0].name
  location            = local.location
  sku_name            = "AZFW_VNet"
  sku_tier            = "Basic"
  firewall_policy_id  = azurerm_firewall_policy.publisher[0].id
  tags                = var.tags
  ip_configuration {
    name                 = "data"
    subnet_id            = azurerm_subnet.firewall[0].id
    public_ip_address_id = azurerm_public_ip.firewall["data"].id
  }
  management_ip_configuration {
    name                 = "management"
    subnet_id            = azurerm_subnet.management[0].id
    public_ip_address_id = azurerm_public_ip.firewall["management"].id
  }
}

resource "azurerm_route_table" "runner" {
  count                         = local.active ? 1 : 0
  name                          = "probe-publisher-forced-egress"
  resource_group_name           = azurerm_resource_group.publisher[0].name
  location                      = local.location
  bgp_route_propagation_enabled = false
  tags                          = var.tags
  route {
    name                   = "all-internet-via-reviewed-firewall"
    address_prefix         = "0.0.0.0/0"
    next_hop_type          = "VirtualAppliance"
    next_hop_in_ip_address = azurerm_firewall.publisher[0].ip_configuration[0].private_ip_address
  }
}

resource "azurerm_subnet_route_table_association" "runner" {
  count          = local.active ? 1 : 0
  subnet_id      = azurerm_subnet.runner[0].id
  route_table_id = azurerm_route_table.runner[0].id
}

# A distinct zone/link prevents this second ACR endpoint from changing DNS for
# the existing executor VNet. There is deliberately no VNet peering.
resource "azurerm_private_dns_zone" "registry" {
  count               = local.active ? 1 : 0
  name                = "privatelink.azurecr.io"
  resource_group_name = azurerm_resource_group.publisher[0].name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "registry" {
  count                 = local.active ? 1 : 0
  name                  = "probe-publisher-registry"
  resource_group_name   = azurerm_resource_group.publisher[0].name
  private_dns_zone_name = azurerm_private_dns_zone.registry[0].name
  virtual_network_id    = azurerm_virtual_network.publisher[0].id
  registration_enabled  = false
  tags                  = var.tags
}

resource "azurerm_private_endpoint" "registry" {
  count               = local.active ? 1 : 0
  name                = "probe-publisher-registry-pe"
  resource_group_name = azurerm_resource_group.publisher[0].name
  location            = local.location
  subnet_id           = azurerm_subnet.registry[0].id
  tags                = var.tags
  private_service_connection {
    name                           = "probe-publisher-registry"
    private_connection_resource_id = var.registry_resource_id
    is_manual_connection           = false
    subresource_names              = ["registry"]
  }
  private_dns_zone_group {
    name                 = "registry"
    private_dns_zone_ids = [azurerm_private_dns_zone.registry[0].id]
  }
  depends_on = [azurerm_private_dns_zone_virtual_network_link.registry]
}

# Registration is a separate operator prerequisite. Callers must configure
# AzAPI skip_provider_registration=true; this module never registers providers.
# Do not connect the organization/runner group before these controls read back.
resource "azapi_resource" "github_network" {
  count     = local.active ? 1 : 0
  type      = "GitHub.Network/networkSettings@2024-04-02"
  name      = "cosmaneura-staging-probe-publisher-network"
  parent_id = azurerm_resource_group.publisher[0].id
  location  = local.location
  body = {
    properties = {
      businessId = var.github_organization_database_id
      subnetId   = azurerm_subnet.runner[0].id
    }
  }
  response_export_values = ["tags.GitHubId"]
  depends_on = [
    azurerm_subnet_network_security_group_association.runner,
    azurerm_subnet_route_table_association.runner,
    azurerm_firewall_policy_rule_collection_group.egress,
    azurerm_private_endpoint.registry,
  ]
}
