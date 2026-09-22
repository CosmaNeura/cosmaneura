# New input regressions only. No live providers, state, Azure plan or apply.
mock_provider "azapi" {}
mock_provider "azurerm" {}

run "ip_literal_is_not_reviewed_hostname" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { reviewed_https_domains = ["192.0.2.1"] }
  expect_failures = [var.reviewed_https_domains]
}

run "oversized_dns_label_is_refused" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { reviewed_https_domains = ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.example"] }
  expect_failures = [var.reviewed_https_domains]
}

run "too_many_domains_are_refused" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { reviewed_https_domains = toset([for n in range(257) : "host${n}.example"]) }
  expect_failures = [var.reviewed_https_domains]
}

run "mixed_case_hostname_is_refused" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { reviewed_https_domains = ["GitHub.com"] }
  expect_failures = [var.reviewed_https_domains]
}

run "non_authorizing_input_does_not_create_resources" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables {
    github_organization_database_id = "123456"
    registry_resource_id            = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/synthetic/providers/Microsoft.ContainerRegistry/registries/syntheticregistry"
    reviewed_https_domains          = ["github.com", "api.github.com"]
  }
  assert {
    condition = (
      !output.proposal.enabled && length(azurerm_resource_group.publisher) == 0 &&
      length(azurerm_subnet.firewall) == 0 && length(azurerm_subnet.management) == 0 &&
      length(azurerm_subnet.runner) == 0 && length(azurerm_subnet.registry) == 0 &&
      length(azapi_resource.github_network) == 0 && length(azurerm_public_ip.firewall) == 0
    )
    error_message = "Exact bindings without both approvals must not create any resource"
  }
}
