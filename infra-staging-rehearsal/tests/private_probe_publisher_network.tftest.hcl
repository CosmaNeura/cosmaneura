# Alternate, uninstantiated module. Mock providers only; no live plan or state.
mock_provider "azapi" {}
mock_provider "azurerm" {}

run "network_default_disabled" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  assert {
    condition = (
      !output.proposal.enabled && length(azurerm_resource_group.publisher) == 0 &&
      length(azapi_resource.github_network) == 0 && length(azurerm_public_ip.firewall) == 0
    )
    error_message = "The unused/default module must not propose any Azure resources"
  }
}

run "enable_requires_approval" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { enabled = true }
  expect_failures = [terraform_data.guard]
}

run "approval_requires_enable" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { exact_network_plan_approved = true }
  expect_failures = [terraform_data.guard]
}

run "enabled_requires_exact_inputs" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables {
    enabled                     = true
    exact_network_plan_approved = true
  }
  expect_failures = [terraform_data.guard]
}

run "wildcards_are_not_reviewed_domains" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { reviewed_https_domains = ["*.github.com"] }
  expect_failures = [var.reviewed_https_domains]
}

run "url_is_not_domain" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { reviewed_https_domains = ["https://github.com"] }
  expect_failures = [var.reviewed_https_domains]
}

run "organization_requires_numeric_database_id" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { github_organization_database_id = "example-org" }
  expect_failures = [var.github_organization_database_id]
}

run "registry_requires_exact_resource_id" {
  command = plan
  module { source = "./modules/private-probe-publisher-network" }
  variables { registry_resource_id = "https://example.azurecr.io" }
  expect_failures = [var.registry_resource_id]
}
