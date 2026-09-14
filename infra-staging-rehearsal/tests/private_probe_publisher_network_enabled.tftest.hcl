# Only synthetic IDs. This plans a standalone alternate module with mocked providers.
mock_provider "azapi" {}
mock_provider "azurerm" {
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic" }
  }
  mock_resource "azurerm_virtual_network" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/virtualNetworks/publisher" }
  }
  mock_resource "azurerm_subnet" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/virtualNetworks/publisher/subnets/synthetic" }
  }
  mock_resource "azurerm_network_security_group" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/networkSecurityGroups/runner" }
  }
  mock_resource "azurerm_firewall_policy" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/firewallPolicies/publisher" }
  }
  mock_resource "azurerm_public_ip" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/publicIPAddresses/synthetic" }
  }
  mock_resource "azurerm_route_table" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/routeTables/runner" }
  }
  mock_resource "azurerm_private_dns_zone" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/privateDnsZones/privatelink.azurecr.io" }
  }
  mock_resource "azurerm_private_endpoint" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/privateEndpoints/registry" }
  }
  mock_resource "azurerm_firewall" {
    defaults = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/azureFirewalls/publisher" }
  }
}

run "reviewed_candidate_is_separate_non_authorizing_network" {
  override_resource {
    target = azurerm_subnet.firewall
    values = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/virtualNetworks/publisher/subnets/AzureFirewallSubnet" }
  }
  override_resource {
    target = azurerm_subnet.management
    values = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/virtualNetworks/publisher/subnets/AzureFirewallManagementSubnet" }
  }
  override_resource {
    target = azurerm_subnet.runner
    values = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/virtualNetworks/publisher/subnets/github-probe-publisher" }
  }
  override_resource {
    target = azurerm_subnet.registry
    values = { id = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.Network/virtualNetworks/publisher/subnets/probe-registry-private-endpoints" }
  }
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables {
    enabled                         = true
    exact_network_plan_approved     = true
    github_organization_database_id = "123456"
    registry_resource_id            = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.ContainerRegistry/registries/syntheticregistry"
    reviewed_https_domains          = ["github.com", "api.github.com", "login.microsoftonline.com"]
  }
  assert {
    condition = (
      output.proposal.enabled && output.proposal.location == "eastus" &&
      output.proposal.vnet_cidr == "10.77.0.0/24" &&
      output.proposal.runner_cidr == "10.77.0.128/27" &&
      output.proposal.registry_endpoint_cidr == "10.77.0.160/27" &&
      !output.proposal.private_reachability_proven && !output.proposal.publisher_containment_proven &&
      !output.proposal.images_published && !output.proposal.runtime_acceptance &&
      !output.proposal.activation_authorized && !output.proposal.existing_registry_managed &&
      !output.proposal.push_grants_managed && !output.proposal.publisher_identity_managed &&
      !output.proposal.github_runner_managed && !output.proposal.github_configuration_managed
    )
    error_message = "Planned network structure must not inflate into live connectivity or publication authority"
  }
  assert {
    condition = (
      length(concat(azurerm_subnet.firewall, azurerm_subnet.management, azurerm_subnet.runner, azurerm_subnet.registry)) == 4 &&
      alltrue([for subnet in concat(azurerm_subnet.firewall, azurerm_subnet.management, azurerm_subnet.runner, azurerm_subnet.registry) : !subnet.default_outbound_access_enabled]) &&
      length(azurerm_subnet.runner[0].delegation) == 1 &&
      length(azurerm_subnet.registry[0].delegation) == 0 &&
      azurerm_network_security_rule.runner["deny_inbound"].access == "Deny" &&
      azurerm_network_security_rule.runner["deny_other"].priority == 4000 &&
      azurerm_network_security_rule.runner["registry"].destination_address_prefix == "10.77.0.160/27" &&
      azurerm_network_security_rule.runner["registry"].destination_port_range == "443"
    )
    error_message = "Only the dedicated runner is delegated and only dedicated registry HTTPS is admitted privately"
  }
  assert {
    condition = (
      azurerm_firewall.publisher[0].sku_tier == "Basic" &&
      azurerm_firewall_policy.publisher[0].threat_intelligence_mode == "Deny" &&
      length(azurerm_firewall.publisher[0].management_ip_configuration) == 1 &&
      !azurerm_route_table.runner[0].bgp_route_propagation_enabled &&
      azapi_resource.github_network[0].type == "GitHub.Network/networkSettings@2024-04-02" &&
      azapi_resource.github_network[0].body.properties.businessId == "123456" &&
      !azurerm_private_dns_zone_virtual_network_link.registry[0].registration_enabled
    )
    error_message = "Private-network control-plane and forced reviewed firewall egress must remain explicit"
  }
  assert {
    condition = (
      azurerm_subnet.firewall[0].name == "AzureFirewallSubnet" &&
      azurerm_subnet.management[0].name == "AzureFirewallManagementSubnet" &&
      azurerm_subnet.runner[0].address_prefixes == tolist(["10.77.0.128/27"]) &&
      azurerm_subnet.registry[0].address_prefixes == tolist(["10.77.0.160/27"]) &&
      length(azurerm_subnet.firewall[0].delegation) == 0 &&
      length(azurerm_subnet.management[0].delegation) == 0 &&
      azurerm_subnet.runner[0].delegation[0].service_delegation[0].name == "GitHub.Network/networkSettings" &&
      azurerm_subnet.registry[0].private_endpoint_network_policies == "Disabled" &&
      azurerm_subnet.runner[0].private_endpoint_network_policies == "Enabled" &&
      azurerm_firewall.publisher[0].ip_configuration[0].subnet_id == azurerm_subnet.firewall[0].id &&
      azurerm_firewall.publisher[0].management_ip_configuration[0].subnet_id == azurerm_subnet.management[0].id &&
      azurerm_private_endpoint.registry[0].subnet_id == azurerm_subnet.registry[0].id &&
      azapi_resource.github_network[0].body.properties.subnetId == azurerm_subnet.runner[0].id
    )
    error_message = "Distinct subnet roles must not be exchanged or delegated to an executor"
  }
  assert {
    condition = (
      azurerm_subnet_network_security_group_association.runner[0].subnet_id == azurerm_subnet.runner[0].id &&
      azurerm_subnet_network_security_group_association.runner[0].network_security_group_id == azurerm_network_security_group.runner[0].id &&
      azurerm_subnet_route_table_association.runner[0].subnet_id == azurerm_subnet.runner[0].id &&
      azurerm_subnet_route_table_association.runner[0].route_table_id == azurerm_route_table.runner[0].id &&
      length(azurerm_route_table.runner[0].route) == 1 &&
      one(azurerm_route_table.runner[0].route).address_prefix == "0.0.0.0/0" &&
      one(azurerm_route_table.runner[0].route).next_hop_type == "VirtualAppliance" &&
      length(azurerm_network_security_rule.runner) == 6 &&
      azurerm_network_security_rule.runner["deny_inbound"].direction == "Inbound" &&
      azurerm_network_security_rule.runner["deny_other"].access == "Deny" &&
      azurerm_network_security_rule.runner["https_routed"].destination_address_prefix == "Internet" &&
      azurerm_network_security_rule.runner["https_routed"].destination_port_range == "443" &&
      azurerm_network_security_rule.runner["dns_udp"].destination_address_prefix == "AzurePlatformDNS" &&
      azurerm_network_security_rule.runner["dns_tcp"].destination_address_prefix == "AzurePlatformDNS"
    )
    error_message = "Runner routing and explicit override of default NSG admission must remain wired"
  }
  assert {
    condition = (
      length(azurerm_firewall_policy_rule_collection_group.egress[0].network_rule_collection) == 0 &&
      length(azurerm_firewall_policy_rule_collection_group.egress[0].nat_rule_collection) == 0 &&
      length(azurerm_firewall_policy_rule_collection_group.egress[0].application_rule_collection) == 1 &&
      azurerm_firewall_policy_rule_collection_group.egress[0].application_rule_collection[0].action == "Allow" &&
      toset(azurerm_firewall_policy_rule_collection_group.egress[0].application_rule_collection[0].rule[0].destination_fqdns) == toset(var.reviewed_https_domains) &&
      length(azurerm_public_ip.firewall) == 2 &&
      azurerm_private_endpoint.registry[0].private_service_connection[0].private_connection_resource_id == var.registry_resource_id &&
      azurerm_private_endpoint.registry[0].private_service_connection[0].subresource_names == tolist(["registry"]) &&
      azurerm_private_endpoint.registry[0].private_dns_zone_group[0].private_dns_zone_ids == tolist([azurerm_private_dns_zone.registry[0].id]) &&
      azurerm_private_dns_zone_virtual_network_link.registry[0].virtual_network_id == azurerm_virtual_network.publisher[0].id &&
      !output.proposal.organization_binding_observed
    )
    error_message = "Only reviewed HTTPS and a separate exact private registry DNS connection may be proposed"
  }
}
