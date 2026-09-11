mock_provider "azapi" {
  mock_resource "azapi_resource" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mock/blobServices/default/containers/mock"
    }
  }
}

mock_provider "azurerm" {
  mock_resource "azurerm_container_registry" {
    defaults = {
      id                       = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.ContainerRegistry/registries/cosmaneurarehearsalexec"
      login_server             = "cosmaneurarehearsalexec.azurecr.io"
      data_endpoint_host_names = ["cosmaneurarehearsalexec.eastus.data.azurecr.io"]
    }
  }

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock"
    }
  }

  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/virtualNetworks/mock"
    }
  }

  mock_resource "azurerm_subnet" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/virtualNetworks/mock/subnets/mock"
    }
  }

  mock_resource "azurerm_private_dns_zone" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
    }
  }

  mock_resource "azurerm_private_endpoint" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/privateEndpoints/mock"
    }
  }

  mock_resource "azurerm_policy_definition" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/providers/Microsoft.Authorization/policyDefinitions/deny-staging-restore-firewall-rules"
    }
  }

  mock_resource "azurerm_role_definition" {
    defaults = {
      role_definition_resource_id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/providers/Microsoft.Authorization/roleDefinitions/11111111-1111-5111-8111-111111111111"
    }
  }

  mock_resource "azurerm_storage_account" {
    defaults = {
      id                = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mock"
      primary_blob_host = "mock.blob.core.windows.net"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      client_id    = "22222222-2222-4222-8222-222222222222"
      id           = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock"
      principal_id = "33333333-3333-4333-8333-333333333333"
      tenant_id    = "d51754d0-bba5-4810-820e-afac542844d6"
    }
  }
}

override_resource {
  target = azurerm_role_definition.marker_reader
  values = {
    role_definition_resource_id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/providers/Microsoft.Authorization/roleDefinitions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  }
}

override_resource {
  target = azurerm_role_definition.marker_create_and_read
  values = {
    role_definition_resource_id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/providers/Microsoft.Authorization/roleDefinitions/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
  }
}

# Separate synthetic job/dispatcher, endpoint-subnet and DNS-zone IDs make
# cross-boundary mix-ups fail. OpenTofu overrides are resource-wide: both job
# instances share this fixture principal, so live readback must prove each OID.
override_resource {
  target = azurerm_user_assigned_identity.job
  values = {
    id           = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.ManagedIdentity/userAssignedIdentities/job"
    principal_id = "44444444-4444-4444-8444-444444444444"
  }
}

override_resource {
  target = azurerm_subnet.private_executor_service_endpoints
  values = {
    id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/virtualNetworks/mock/subnets/private-executor-service-endpoints"
  }
}

override_resource {
  target = azurerm_private_dns_zone.private_executor_registry
  values = {
    id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/privateDnsZones/privatelink.azurecr.io"
  }
}

run "default_is_dormant" {
  command = plan

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 0 &&
      length(azapi_resource.private_launch_mailbox_container) == 0 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 0 &&
      !output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account == null
    )
    error_message = "No launch mailbox may exist without its independent storage approval"
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 0 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 0 &&
      length(azapi_resource.private_evidence_container) == 0 &&
      length(azurerm_private_endpoint.private_evidence) == 0 &&
      !output.private_evidence_storage_contract.enabled &&
      output.private_evidence_storage_contract.account == null
    )
    error_message = "No evidence storage or private evidence path may exist without its separate approval"
  }

  assert {
    condition = (
      length(azapi_resource.private_executor_vault) == 0 &&
      length(azurerm_private_endpoint.private_executor_vault) == 0 &&
      length(azurerm_private_dns_zone.private_executor_vault) == 0 &&
      length(azurerm_private_dns_zone_virtual_network_link.private_executor_vault) == 0 &&
      !output.private_executor_vault_contract.enabled &&
      output.private_executor_vault_contract.vault == null
    )
    error_message = "No vault or private vault path may exist in the default plan"
  }

  assert {
    condition     = length(terraform_data.activation_guard) == 0 && length(terraform_data.private_executor_topology_blocker) == 0
    error_message = "The default plan must not create Terraform activation resources"
  }

  assert {
    condition     = length(azurerm_resource_group.control) == 0 && length(azurerm_resource_group.target) == 0
    error_message = "The default plan must not create Azure resource groups"
  }

  assert {
    condition     = length(azurerm_virtual_network.control) == 0 && length(azurerm_subnet.marker_private_endpoint) == 0 && length(azurerm_subnet.private_executor_reserved) == 0 && length(azurerm_subnet.private_executor_service_endpoints) == 0 && length(azurerm_private_dns_zone.marker_blob) == 0 && length(azurerm_private_dns_zone_virtual_network_link.marker_blob) == 0 && length(azurerm_private_endpoint.marker) == 0
    error_message = "The default plan must not create network or private-link resources"
  }

  assert {
    condition     = length(azurerm_storage_account.marker) == 0 && length(azapi_resource.marker_container) == 0
    error_message = "The default plan must not create the marker or evidence store"
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.dispatcher) == 0 && length(azurerm_user_assigned_identity.job) == 0 && length(azurerm_federated_identity_credential.dispatcher) == 0
    error_message = "The default plan must not create dispatcher, job, or federated identities"
  }

  assert {
    condition     = length(azurerm_policy_definition.deny_postgres_firewall_rules) == 0 && length(azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules) == 0
    error_message = "The default plan must not create policy resources"
  }

  assert {
    condition     = length(azurerm_role_definition.control_reader) == 0 && length(azurerm_role_definition.source_reader) == 0 && length(azurerm_role_definition.target_provisioner) == 0 && length(azurerm_role_definition.target_cleanup) == 0 && length(azurerm_role_definition.marker_reader) == 0 && length(azurerm_role_definition.marker_create_and_read) == 0
    error_message = "The default plan must not create custom roles"
  }

  assert {
    condition     = length(azurerm_role_assignment.control_reader) == 0 && length(azurerm_role_assignment.source_reader) == 0 && length(azurerm_role_assignment.target_provisioner) == 0 && length(azurerm_role_assignment.target_cleanup) == 0 && length(azurerm_role_assignment.marker_read) == 0 && length(azurerm_role_assignment.marker_provision_cleanup) == 0 && length(azurerm_role_assignment.marker_create) == 0
    error_message = "The default plan must not create role assignments"
  }

  assert {
    condition     = length(azapi_resource_action.marker_legal_hold) == 0 && length(azurerm_storage_container_immutability_policy.marker) == 0 && length(azurerm_management_lock.marker) == 0
    error_message = "Every retention control must remain off by default"
  }

  assert {
    condition     = output.marker_store_binding == null && length(output.restore_identity_bindings) == 0
    error_message = "Default-off outputs must not imply live marker or identity bindings"
  }

  assert {
    condition     = !output.private_executor_network_contract.enabled && output.private_executor_network_contract.vnet_resource_id == null && output.private_executor_network_contract.subnets.private_service_endpoints.resource_id == null
    error_message = "The default outputs must not imply an enabled or deployed private-executor network"
  }

  assert {
    condition = (
      length(azurerm_container_registry.private_executor) == 0 &&
      length(azurerm_private_endpoint.private_executor_registry) == 0 &&
      length(azurerm_private_dns_zone.private_executor_registry) == 0 &&
      length(azurerm_private_dns_zone_virtual_network_link.private_executor_registry) == 0 &&
      length(azurerm_role_assignment.private_executor_acrpull) == 0 &&
      !output.private_executor_registry_contract.enabled &&
      output.private_executor_registry_contract.registry == null
    )
    error_message = "The default plan must create no registry, private path or pull grants"
  }
}

run "reversible_control_plane" {
  command = plan

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 0 &&
      length(azapi_resource.private_launch_mailbox_container) == 0 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 0 &&
      !output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account == null
    )
    error_message = "No launch mailbox may exist without its independent storage approval"
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 0 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 0 &&
      length(azapi_resource.private_evidence_container) == 0 &&
      length(azurerm_private_endpoint.private_evidence) == 0 &&
      !output.private_evidence_storage_contract.enabled &&
      output.private_evidence_storage_contract.account == null
    )
    error_message = "No evidence storage or private evidence path may exist without its separate approval"
  }

  assert {
    condition     = length(azapi_resource.private_executor_vault) == 0 && output.private_executor_vault_contract.vault == null
    error_message = "The base alone must not create a vault"
  }

  variables {
    deploy_staging_restore_control_plane = true
    target_cidr_inventory_revalidated    = true
    control_cidr_inventory_revalidated   = true
  }

  assert {
    condition = (
      length(azurerm_container_registry.private_executor) == 0 &&
      length(azurerm_private_endpoint.private_executor_registry) == 0 &&
      length(azurerm_private_dns_zone.private_executor_registry) == 0 &&
      length(azurerm_private_dns_zone_virtual_network_link.private_executor_registry) == 0 &&
      length(azurerm_role_assignment.private_executor_acrpull) == 0 &&
      output.private_executor_registry_contract.registry == null
    )
    error_message = "The approved base alone must not create any registry resources"
  }

  assert {
    condition     = azurerm_resource_group.target[0].name == "cosmaneura-staging-rehearsal" && azurerm_resource_group.target[0].location == "northcentralus"
    error_message = "The controller's persistent target RG identity must remain exact"
  }

  assert {
    condition     = output.target_topology_contract.vnet_address_space == "10.75.0.0/24" && output.target_topology_contract.private_endpoint_subnet_prefix == "10.75.0.0/27" && output.target_topology_contract.owned_by_this_root == false
    error_message = "The ephemeral target network contract must use the revalidated 10.75 range without becoming state-owned"
  }

  assert {
    condition     = length(azurerm_virtual_network.control[0].address_space) == 1 && contains(azurerm_virtual_network.control[0].address_space, "10.76.0.0/24") && length(azurerm_subnet.private_executor_reserved[0].address_prefixes) == 1 && azurerm_subnet.private_executor_reserved[0].address_prefixes[0] == "10.76.0.32/27" && length(azurerm_subnet.private_executor_reserved[0].delegation) == 0 && length(azurerm_subnet.private_executor_service_endpoints) == 0
    error_message = "The persistent control network and reserved executor subnet must remain distinct from the target"
  }

  assert {
    condition     = output.private_executor_blocker.implemented == false && output.private_executor_blocker.network_foundation_enabled == false && output.private_executor_blocker.ephemeral_target_reachability == "not-implemented"
    error_message = "A subnet reservation must not claim private-executor or target reachability"
  }

  assert {
    condition     = azurerm_storage_account.marker[0].account_tier == "Standard" && azurerm_storage_account.marker[0].account_replication_type == "ZRS" && azurerm_storage_account.marker[0].location == "eastus"
    error_message = "The marker account must remain Standard_ZRS in East US"
  }

  assert {
    condition     = azurerm_storage_account.marker[0].public_network_access_enabled == false && azurerm_storage_account.marker[0].shared_access_key_enabled == false && azurerm_storage_account.marker[0].default_to_oauth_authentication == true
    error_message = "The marker account must remain private, OAuth-default, and shared-key disabled"
  }

  assert {
    condition     = toset(keys(azapi_resource.marker_container)) == toset(["restore-intents-v3", "restore-submissions-v3", "restore-cleanups-v3", "restore-intents-v4", "restore-submissions-v4", "restore-cleanups-v4", "restore-evidence-v1"])
    error_message = "Both isolated marker namespaces and the reserved evidence container must be managed through Azure Resource Manager"
  }

  assert {
    condition     = azurerm_federated_identity_credential.dispatcher["provision"].subject == "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision" && azurerm_federated_identity_credential.dispatcher["cleanup"].subject == "repo:CosmaNeura/cosmaneura:environment:staging-restore-cleanup" && length(azurerm_user_assigned_identity.job) == 2
    error_message = "Only dispatcher UAMIs may trust the exact GitHub environment subjects; separate job UAMIs must exist without FICs"
  }

  assert {
    condition     = azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0].resource_group_id == azurerm_resource_group.target[0].id && azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0].enforce
    error_message = "The firewall-rule deny policy must be enforced at the exact target RG"
  }

  assert {
    condition     = length(azurerm_role_definition.marker_create_and_read) == 0 && length(azurerm_role_assignment.marker_create) == 0
    error_message = "Unproven marker-create authorization must remain absent from the reversible base rung"
  }

  assert {
    condition     = length(azurerm_role_assignment.marker_read) == 2 && length(azurerm_role_assignment.marker_provision_cleanup) == 1 && output.marker_store_binding.role_assignment_ids.provision_intent == null && output.marker_store_binding.role_assignment_ids.provision_submission == null && output.marker_store_binding.role_assignment_ids.provision_cleanup == azurerm_role_assignment.marker_provision_cleanup[0].id && output.marker_store_binding.role_assignment_ids.cleanup_marker == null && !output.marker_store_binding.private_marker_network_posture_proven && !output.marker_store_binding.legal_hold_readback_proven && length(output.marker_store_binding.immutability_policy_ids) == 0 && output.marker_store_binding.evidence_immutability_policy_id == null
    error_message = "Create-role and immutability IDs must remain null or empty while the one logical provision-cleanup output preserves its read-only grant"
  }

  assert {
    condition     = azurerm_role_assignment.marker_provision_cleanup[0].role_definition_id == azurerm_role_definition.marker_reader[0].role_definition_resource_id
    error_message = "The dormant provision_cleanup assignment must retain marker-reader authority"
  }
}

run "private_executor_network_requires_exact_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "approved_private_executor_network_foundation" {
  command = plan

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 0 &&
      length(azapi_resource.private_launch_mailbox_container) == 0 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 0 &&
      !output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account == null
    )
    error_message = "No launch mailbox may exist without its independent storage approval"
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 0 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 0 &&
      length(azapi_resource.private_evidence_container) == 0 &&
      length(azurerm_private_endpoint.private_evidence) == 0 &&
      !output.private_evidence_storage_contract.enabled &&
      output.private_evidence_storage_contract.account == null
    )
    error_message = "No evidence storage or private evidence path may exist without its separate approval"
  }

  assert {
    condition     = length(azapi_resource.private_executor_vault) == 0 && output.private_executor_vault_contract.vault == null
    error_message = "Network approval alone must not create a vault"
  }

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
  }

  assert {
    condition = (
      length(azurerm_container_registry.private_executor) == 0 &&
      length(azurerm_private_endpoint.private_executor_registry) == 0 &&
      length(azurerm_private_dns_zone.private_executor_registry) == 0 &&
      length(azurerm_private_dns_zone_virtual_network_link.private_executor_registry) == 0 &&
      length(azurerm_role_assignment.private_executor_acrpull) == 0 &&
      output.private_executor_registry_contract.registry == null
    )
    error_message = "The independently approved network rung must not implicitly create registry resources"
  }

  assert {
    condition     = azurerm_virtual_network.control[0].location == "eastus" && azurerm_virtual_network.control[0].address_space == toset(["10.76.0.0/24"])
    error_message = "The executor foundation must remain in the exact East US control VNet"
  }

  assert {
    condition     = azurerm_subnet.marker_private_endpoint[0].address_prefixes == tolist(["10.76.0.0/27"]) && !azurerm_subnet.marker_private_endpoint[0].default_outbound_access_enabled && azurerm_subnet.marker_private_endpoint[0].private_endpoint_network_policies == "Disabled"
    error_message = "The first /27 must remain the outbound-disabled Blob private-endpoint subnet"
  }

  assert {
    condition = (
      azurerm_subnet.private_executor_reserved[0].address_prefixes == tolist(["10.76.0.32/27"]) &&
      !azurerm_subnet.private_executor_reserved[0].default_outbound_access_enabled &&
      length(azurerm_subnet.private_executor_reserved[0].delegation) == 1 &&
      alltrue([
        for delegation in azurerm_subnet.private_executor_reserved[0].delegation :
        delegation.service_delegation[0].name == "Microsoft.App/environments" &&
        delegation.service_delegation[0].actions == toset(["Microsoft.Network/virtualNetworks/subnets/join/action"])
      ])
    )
    error_message = "The second /27 must be outbound-disabled and delegated only to Container Apps environments"
  }

  assert {
    condition = (
      length(azurerm_subnet.private_executor_service_endpoints) == 1 &&
      azurerm_subnet.private_executor_service_endpoints[0].address_prefixes == tolist(["10.76.0.64/27"]) &&
      !azurerm_subnet.private_executor_service_endpoints[0].default_outbound_access_enabled &&
      azurerm_subnet.private_executor_service_endpoints[0].private_endpoint_network_policies == "Disabled" &&
      azurerm_subnet.private_executor_service_endpoints[0].private_link_service_network_policies_enabled &&
      length(azurerm_subnet.private_executor_service_endpoints[0].delegation) == 0 &&
      try(length(azurerm_subnet.private_executor_service_endpoints[0].service_endpoints), 0) == 0
    )
    error_message = "The third /27 must be an undelegated, outbound-disabled private-endpoint subnet"
  }

  assert {
    condition = (
      output.private_executor_network_contract.enabled &&
      !output.private_executor_network_contract.vnet_peering &&
      !output.private_executor_network_contract.public_ingress &&
      !output.private_executor_network_contract.route_table &&
      !output.private_executor_network_contract.nat_gateway &&
      !output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_network_contract.private_endpoints &&
      output.private_executor_blocker.implemented == false
    )
    error_message = "The network foundation must not claim compute, endpoints, routing, ingress, peering, or executor binding"
  }
}

run "registry_requires_exact_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_registry           = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_rejects_approval_without_enable" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    private_executor_registry_plan_approved    = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_requires_base" {
  command = plan

  variables {
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_registry           = true
    private_executor_registry_plan_approved    = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_approval_alone_requires_base" {
  command = plan

  variables {
    private_executor_registry_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_requires_network_foundation" {
  command = plan

  variables {
    deploy_staging_restore_control_plane    = true
    target_cidr_inventory_revalidated       = true
    control_cidr_inventory_revalidated      = true
    enable_private_executor_registry        = true
    private_executor_registry_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_requires_network_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = false
    enable_private_executor_registry           = true
    private_executor_registry_plan_approved    = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_requires_control_cidr_inventory" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = false
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_registry           = true
    private_executor_registry_plan_approved    = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_requires_target_cidr_inventory" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = false
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_registry           = true
    private_executor_registry_plan_approved    = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "registry_name_is_lowercase_alphanumeric" {
  command = plan

  variables {
    private_executor_registry_name = "invalid-registry"
  }

  expect_failures = [var.private_executor_registry_name]
}

run "approved_private_registry_is_private_and_job_only" {
  command = plan

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 0 &&
      length(azapi_resource.private_launch_mailbox_container) == 0 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 0 &&
      !output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account == null
    )
    error_message = "No launch mailbox may exist without its independent storage approval"
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 0 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 0 &&
      length(azapi_resource.private_evidence_container) == 0 &&
      length(azurerm_private_endpoint.private_evidence) == 0 &&
      !output.private_evidence_storage_contract.enabled &&
      output.private_evidence_storage_contract.account == null
    )
    error_message = "No evidence storage or private evidence path may exist without its separate approval"
  }

  assert {
    condition     = length(azapi_resource.private_executor_vault) == 0 && output.private_executor_vault_contract.vault == null
    error_message = "Registry approval must not imply vault or irreversible purge-protection approval"
  }

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_registry           = true
    private_executor_registry_plan_approved    = true
  }

  assert {
    condition = (
      length(azurerm_container_registry.private_executor) == 1 &&
      azurerm_container_registry.private_executor[0].name == "cosmaneurarehearsalexec" &&
      azurerm_container_registry.private_executor[0].sku == "Premium" &&
      azurerm_container_registry.private_executor[0].location == "eastus" &&
      azurerm_container_registry.private_executor[0].resource_group_name == azurerm_resource_group.control[0].name &&
      !azurerm_container_registry.private_executor[0].admin_enabled &&
      !azurerm_container_registry.private_executor[0].anonymous_pull_enabled &&
      !azurerm_container_registry.private_executor[0].public_network_access_enabled &&
      azurerm_container_registry.private_executor[0].network_rule_bypass_option == "None" &&
      azurerm_container_registry.private_executor[0].data_endpoint_enabled &&
      !azurerm_container_registry.private_executor[0].export_policy_enabled &&
      length(azurerm_container_registry.private_executor[0].network_rule_set) == 1 &&
      azurerm_container_registry.private_executor[0].network_rule_set[0].default_action == "Deny" &&
      length(azurerm_container_registry.private_executor[0].network_rule_set[0].ip_rule) == 0 &&
      length(azurerm_container_registry.private_executor[0].identity) == 0 &&
      length(azurerm_container_registry.private_executor[0].georeplications) == 0
    )
    error_message = "Only the dedicated East US private Premium registry, without credentials, bypasses, export, replicas or public access, may be modeled"
  }

  assert {
    condition = (
      length(azurerm_private_endpoint.private_executor_registry) == 1 &&
      azurerm_private_endpoint.private_executor_registry[0].resource_group_name == azurerm_resource_group.control[0].name &&
      azurerm_private_endpoint.private_executor_registry[0].subnet_id == azurerm_subnet.private_executor_service_endpoints[0].id &&
      azurerm_private_endpoint.private_executor_registry[0].subnet_id != azurerm_subnet.marker_private_endpoint[0].id &&
      length(azurerm_private_endpoint.private_executor_registry[0].private_service_connection) == 1 &&
      azurerm_private_endpoint.private_executor_registry[0].private_service_connection[0].private_connection_resource_id == azurerm_container_registry.private_executor[0].id &&
      !azurerm_private_endpoint.private_executor_registry[0].private_service_connection[0].is_manual_connection &&
      azurerm_private_endpoint.private_executor_registry[0].private_service_connection[0].subresource_names == tolist(["registry"]) &&
      length(azurerm_private_endpoint.private_executor_registry[0].private_dns_zone_group) == 1 &&
      azurerm_private_endpoint.private_executor_registry[0].private_dns_zone_group[0].name == "registry" &&
      toset(azurerm_private_endpoint.private_executor_registry[0].private_dns_zone_group[0].private_dns_zone_ids) == toset([azurerm_private_dns_zone.private_executor_registry[0].id])
    )
    error_message = "The only registry connection and zone group must bind the exact registry and service-endpoint subnet"
  }

  assert {
    condition = (
      length(azurerm_private_dns_zone.private_executor_registry) == 1 &&
      azurerm_private_dns_zone.private_executor_registry[0].name == "privatelink.azurecr.io" &&
      azurerm_private_dns_zone.private_executor_registry[0].resource_group_name == azurerm_resource_group.control[0].name &&
      length(azurerm_private_dns_zone_virtual_network_link.private_executor_registry) == 1 &&
      azurerm_private_dns_zone_virtual_network_link.private_executor_registry[0].private_dns_zone_name == "privatelink.azurecr.io" &&
      azurerm_private_dns_zone_virtual_network_link.private_executor_registry[0].resource_group_name == azurerm_resource_group.control[0].name &&
      azurerm_private_dns_zone_virtual_network_link.private_executor_registry[0].virtual_network_id == azurerm_virtual_network.control[0].id &&
      !azurerm_private_dns_zone_virtual_network_link.private_executor_registry[0].registration_enabled
    )
    error_message = "Private ACR DNS must link only the control VNet without registration"
  }

  assert {
    condition = (
      toset(keys(azurerm_role_assignment.private_executor_acrpull)) == toset(["provision", "cleanup"]) &&
      alltrue([
        for key, assignment in azurerm_role_assignment.private_executor_acrpull :
        assignment.scope == azurerm_container_registry.private_executor[0].id &&
        assignment.role_definition_id == "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d" &&
        assignment.principal_id == azurerm_user_assigned_identity.job[key].principal_id &&
        assignment.principal_type == "ServicePrincipal" &&
        alltrue([for dispatcher in azurerm_user_assigned_identity.dispatcher : assignment.principal_id != dispatcher.principal_id])
      ])
    )
    error_message = "Exactly the two unfederated job principals, never dispatchers, may receive registry-scoped built-in AcrPull"
  }

  assert {
    condition = (
      output.private_executor_registry_contract.enabled &&
      output.private_executor_registry_contract.registry.resource_id == azurerm_container_registry.private_executor[0].id &&
      output.private_executor_registry_contract.registry.host == "cosmaneurarehearsalexec.azurecr.io" &&
      toset(output.private_executor_registry_contract.registry.data_hosts) == toset(["cosmaneurarehearsalexec.eastus.data.azurecr.io"]) &&
      !output.private_executor_registry_contract.runtime_binding_complete &&
      !output.private_executor_registry_contract.private_pull_proven &&
      !output.private_executor_registry_contract.image_publication_proven &&
      !output.private_executor_registry_contract.role_assignment_mode_verified &&
      output.private_executor_registry_contract.role_assignment_mode_requirement == "LegacyRegistryPermissions" &&
      !output.private_executor_blocker.implemented &&
      output.private_executor_blocker.trusted_evidence_binding == "not-implemented" &&
      !terraform_data.activation_guard[0].input.private_executor_binding &&
      !terraform_data.activation_guard[0].input.trusted_evidence_binding &&
      !output.target_topology_contract.owned_by_this_root &&
      output.private_executor_network_contract.private_endpoints &&
      !output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_network_contract.vnet_peering &&
      !output.private_executor_network_contract.public_ingress
    )
    error_message = "Registry source identifiers must not claim verified permissions, private pull, image, runtime or target connectivity"
  }

  assert {
    condition = (
      length(azurerm_user_assigned_identity.job) == 2 &&
      length(azurerm_user_assigned_identity.dispatcher) == 2 &&
      length(azurerm_federated_identity_credential.dispatcher) == 2 &&
      length(azapi_resource.marker_container) == 7 &&
      length(azurerm_role_assignment.marker_create) == 0 &&
      length(azurerm_role_definition.marker_create_and_read) == 0 &&
      length(azapi_resource_action.marker_legal_hold) == 0 &&
      length(azurerm_storage_container_immutability_policy.marker) == 0 &&
      length(azurerm_management_lock.marker) == 0
    )
    error_message = "Existing identities and retained containers must remain; marker-write and retention controls must stay absent"
  }
}

override_resource {
  target = azapi_resource.private_executor_vault
  values = {
    id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.KeyVault/vaults/cosmaneura-rehearsal-kv"
    output = {
      properties = {
        vaultUri = "https://cosmaneura-rehearsal-kv.vault.azure.net/"
      }
    }
  }
}

override_resource {
  target = azurerm_private_dns_zone.private_executor_vault
  values = {
    id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Network/privateDnsZones/privatelink.vaultcore.azure.net"
  }
}

run "vault_requires_plan_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = true
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = false
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_requires_irreversible_purge_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = true
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = false
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_requires_base" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = false
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = true
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_requires_network" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = false
    private_executor_network_plan_approved           = false
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_requires_network_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = false
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_requires_target_cidr" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = false
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = true
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_requires_control_cidr" {
  command = plan

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = false
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = true
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_plan_approval_alone_is_rejected" {
  command = plan

  variables {
    private_executor_vault_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_purge_approval_alone_is_rejected" {
  command = plan

  variables {
    private_executor_vault_purge_protection_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_enable_alone_is_rejected" {
  command = plan

  variables {
    enable_private_executor_vault = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "vault_name_rejects_consecutive_hyphens" {
  command = plan

  variables {
    private_executor_vault_name = "invalid--vault"
  }

  expect_failures = [var.private_executor_vault_name]
}

run "approved_vault_is_empty_private_and_arm_only" {
  command = plan

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 0 &&
      length(azapi_resource.private_launch_mailbox_container) == 0 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 0 &&
      !output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account == null
    )
    error_message = "No launch mailbox may exist without its independent storage approval"
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 0 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 0 &&
      length(azapi_resource.private_evidence_container) == 0 &&
      length(azurerm_private_endpoint.private_evidence) == 0 &&
      !output.private_evidence_storage_contract.enabled &&
      output.private_evidence_storage_contract.account == null
    )
    error_message = "No evidence storage or private evidence path may exist without its separate approval"
  }

  variables {
    deploy_staging_restore_control_plane             = true
    target_cidr_inventory_revalidated                = true
    control_cidr_inventory_revalidated               = true
    enable_private_executor_network_foundation       = true
    private_executor_network_plan_approved           = true
    enable_private_executor_vault                    = true
    private_executor_vault_plan_approved             = true
    private_executor_vault_purge_protection_approved = true
  }

  assert {
    condition = (
      length(azapi_resource.private_executor_vault) == 1 &&
      azapi_resource.private_executor_vault[0].type == "Microsoft.KeyVault/vaults@2023-07-01" &&
      azapi_resource.private_executor_vault[0].name == "cosmaneura-rehearsal-kv" &&
      azapi_resource.private_executor_vault[0].parent_id == azurerm_resource_group.control[0].id &&
      azapi_resource.private_executor_vault[0].location == "eastus" &&
      azapi_resource.private_executor_vault[0].schema_validation_enabled &&
      toset(azapi_resource.private_executor_vault[0].response_export_values) == toset(["properties.vaultUri"]) &&
      toset(keys(azapi_resource.private_executor_vault[0].body)) == toset(["properties"]) &&
      toset(keys(azapi_resource.private_executor_vault[0].body.properties)) == toset([
        "tenantId", "sku", "createMode", "accessPolicies", "enableRbacAuthorization",
        "enabledForDeployment", "enabledForDiskEncryption", "enabledForTemplateDeployment",
        "publicNetworkAccess", "enableSoftDelete", "softDeleteRetentionInDays",
        "enablePurgeProtection", "networkAcls"
      ])
    )
    error_message = "Only the exact ARM-only control-group vault body and non-secret URI export are allowed"
  }

  assert {
    condition = (
      azapi_resource.private_executor_vault[0].body.properties.tenantId == "d51754d0-bba5-4810-820e-afac542844d6" &&
      azapi_resource.private_executor_vault[0].body.properties.sku.family == "A" &&
      azapi_resource.private_executor_vault[0].body.properties.sku.name == "standard" &&
      azapi_resource.private_executor_vault[0].body.properties.createMode == "default" &&
      length(azapi_resource.private_executor_vault[0].body.properties.accessPolicies) == 0 &&
      azapi_resource.private_executor_vault[0].body.properties.enableRbacAuthorization &&
      !azapi_resource.private_executor_vault[0].body.properties.enabledForDeployment &&
      !azapi_resource.private_executor_vault[0].body.properties.enabledForDiskEncryption &&
      !azapi_resource.private_executor_vault[0].body.properties.enabledForTemplateDeployment &&
      azapi_resource.private_executor_vault[0].body.properties.publicNetworkAccess == "Disabled" &&
      azapi_resource.private_executor_vault[0].body.properties.enableSoftDelete &&
      azapi_resource.private_executor_vault[0].body.properties.softDeleteRetentionInDays == 90 &&
      azapi_resource.private_executor_vault[0].body.properties.enablePurgeProtection &&
      azapi_resource.private_executor_vault[0].body.properties.networkAcls.bypass == "None" &&
      azapi_resource.private_executor_vault[0].body.properties.networkAcls.defaultAction == "Deny" &&
      length(azapi_resource.private_executor_vault[0].body.properties.networkAcls.ipRules) == 0 &&
      length(azapi_resource.private_executor_vault[0].body.properties.networkAcls.virtualNetworkRules) == 0
    )
    error_message = "The vault must have exact tenant/RBAC, no access policies or bypasses, and explicitly approved 90-day irreversible purge protection"
  }

  assert {
    condition = (
      length(azurerm_private_endpoint.private_executor_vault) == 1 &&
      azurerm_private_endpoint.private_executor_vault[0].resource_group_name == azurerm_resource_group.control[0].name &&
      azurerm_private_endpoint.private_executor_vault[0].subnet_id == azurerm_subnet.private_executor_service_endpoints[0].id &&
      azurerm_private_endpoint.private_executor_vault[0].subnet_id != azurerm_subnet.marker_private_endpoint[0].id &&
      length(azurerm_private_endpoint.private_executor_vault[0].private_service_connection) == 1 &&
      azurerm_private_endpoint.private_executor_vault[0].private_service_connection[0].private_connection_resource_id == azapi_resource.private_executor_vault[0].id &&
      azurerm_private_endpoint.private_executor_vault[0].private_service_connection[0].subresource_names == tolist(["vault"]) &&
      !azurerm_private_endpoint.private_executor_vault[0].private_service_connection[0].is_manual_connection &&
      length(azurerm_private_endpoint.private_executor_vault[0].private_dns_zone_group) == 1 &&
      azurerm_private_endpoint.private_executor_vault[0].private_dns_zone_group[0].name == "vault" &&
      toset(azurerm_private_endpoint.private_executor_vault[0].private_dns_zone_group[0].private_dns_zone_ids) == toset([azurerm_private_dns_zone.private_executor_vault[0].id])
    )
    error_message = "The vault endpoint must bind only this vault, its dedicated zone and the control private-service subnet"
  }

  assert {
    condition = (
      length(azurerm_private_dns_zone.private_executor_vault) == 1 &&
      azurerm_private_dns_zone.private_executor_vault[0].name == "privatelink.vaultcore.azure.net" &&
      azurerm_private_dns_zone.private_executor_vault[0].resource_group_name == azurerm_resource_group.control[0].name &&
      length(azurerm_private_dns_zone_virtual_network_link.private_executor_vault) == 1 &&
      azurerm_private_dns_zone_virtual_network_link.private_executor_vault[0].resource_group_name == azurerm_resource_group.control[0].name &&
      azurerm_private_dns_zone_virtual_network_link.private_executor_vault[0].private_dns_zone_name == "privatelink.vaultcore.azure.net" &&
      azurerm_private_dns_zone_virtual_network_link.private_executor_vault[0].virtual_network_id == azurerm_virtual_network.control[0].id &&
      !azurerm_private_dns_zone_virtual_network_link.private_executor_vault[0].registration_enabled
    )
    error_message = "The private vault zone must link only the control VNet without registration"
  }

  assert {
    condition = (
      output.private_executor_vault_contract.enabled &&
      output.private_executor_vault_contract.vault.resource_id == azapi_resource.private_executor_vault[0].id &&
      output.private_executor_vault_contract.vault.uri == "https://cosmaneura-rehearsal-kv.vault.azure.net/" &&
      !output.private_executor_vault_contract.private_secret_binding_complete &&
      !output.private_executor_vault_contract.secret_material_managed &&
      !output.private_executor_vault_contract.secret_read_grants_managed &&
      !output.private_executor_vault_contract.private_get_proven &&
      !output.private_executor_blocker.implemented &&
      !terraform_data.activation_guard[0].input.private_executor_binding &&
      !terraform_data.activation_guard[0].input.trusted_evidence_binding &&
      output.private_executor_network_contract.private_endpoints &&
      !output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_network_contract.vnet_peering &&
      !output.private_executor_network_contract.public_ingress &&
      !output.private_executor_registry_contract.enabled &&
      !output.target_topology_contract.owned_by_this_root &&
      length(azapi_resource.marker_container) == 7 &&
      length(azurerm_role_assignment.marker_create) == 0 &&
      length(azapi_resource_action.marker_legal_hold) == 0 &&
      length(azurerm_storage_container_immutability_policy.marker) == 0 &&
      length(azurerm_management_lock.marker) == 0
    )
    error_message = "Vault source cannot claim live secrets, read grants, connectivity or runtime, and must not activate registry/marker retention or target resources"
  }
}

override_resource {
  target = azapi_resource.private_evidence_account
  values = {
    id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/cosmaneurarehearsalevd"
    output = {
      properties = {
        primaryEndpoints = {
          blob = "https://cosmaneurarehearsalevd.blob.core.windows.net/"
        }
      }
    }
  }
}

run "evidence_requires_plan_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = false
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_rejects_approval_without_enable" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = false
    private_evidence_storage_plan_approved     = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_requires_base" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = false
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_requires_network" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = false
    private_executor_network_plan_approved     = false
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_requires_network_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = false
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_requires_control_cidr" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = false
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_requires_target_cidr" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = false
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_enable_alone_requires_base" {
  command = plan

  variables {
    enable_private_evidence_storage = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_approval_alone_requires_base" {
  command = plan

  variables {
    private_evidence_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_rejects_marker_account_name" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
    private_evidence_storage_account_name      = "cosmaneurarehearsalctl"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "evidence_rejects_invalid_account_name" {
  command = plan

  variables {
    private_evidence_storage_account_name = "invalid-account"
  }

  expect_failures = [var.private_evidence_storage_account_name]
}

run "approved_evidence_is_empty_private_versioned_and_separate" {
  command = plan

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 0 &&
      length(azapi_resource.private_launch_mailbox_container) == 0 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 0 &&
      !output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account == null
    )
    error_message = "No launch mailbox may exist without its independent storage approval"
  }

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_evidence_storage            = true
    private_evidence_storage_plan_approved     = true
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 1 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 1 &&
      length(azapi_resource.private_evidence_container) == 2 &&
      length(azurerm_private_endpoint.private_evidence) == 1 &&
      azapi_resource.private_evidence_account[0].type == "Microsoft.Storage/storageAccounts@2023-05-01" &&
      azapi_resource.private_evidence_account[0].name == "cosmaneurarehearsalevd" &&
      azapi_resource.private_evidence_account[0].name != azurerm_storage_account.marker[0].name &&
      azapi_resource.private_evidence_account[0].id != azurerm_storage_account.marker[0].id &&
      azapi_resource.private_evidence_account[0].parent_id == azurerm_resource_group.control[0].id &&
      azapi_resource.private_evidence_account[0].location == "eastus" &&
      azapi_resource.private_evidence_account[0].schema_validation_enabled &&
      toset(azapi_resource.private_evidence_account[0].response_export_values) == toset(["properties.primaryEndpoints.blob"])
    )
    error_message = "Exactly five dedicated control-group managed instances are allowed, with only the non-secret account Blob endpoint exported"
  }

  assert {
    condition = jsonencode(azapi_resource.private_evidence_account[0].body) == jsonencode({
      kind = "StorageV2"
      sku  = { name = "Standard_ZRS" }
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
    })
    error_message = "The account body must exactly enforce private OAuth-only TLS, encryption and zero network exceptions or auxiliary access paths"
  }

  assert {
    condition = (
      azapi_update_resource.private_evidence_blob_versioning[0].type == "Microsoft.Storage/storageAccounts/blobServices@2023-05-01" &&
      azapi_update_resource.private_evidence_blob_versioning[0].resource_id == "${azapi_resource.private_evidence_account[0].id}/blobServices/default" &&
      jsonencode(azapi_update_resource.private_evidence_blob_versioning[0].body) == jsonencode({ properties = { isVersioningEnabled = true } }) &&
      length(azapi_update_resource.private_evidence_blob_versioning[0].response_export_values) == 0 &&
      toset(keys(azapi_resource.private_evidence_container)) == toset(["restore-evidence-v1", "restore-runtime-bindings-v1"]) &&
      alltrue([for name, container in azapi_resource.private_evidence_container : (
        container.name == name &&
        container.type == "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01" &&
        container.parent_id == "${azapi_resource.private_evidence_account[0].id}/blobServices/default" &&
        container.parent_id != "${azurerm_storage_account.marker[0].id}/blobServices/default" &&
        jsonencode(container.body) == jsonencode({ properties = { publicAccess = "None" } }) &&
        container.schema_validation_enabled &&
        length(container.response_export_values) == 0
      )])
    )
    error_message = "Versioning must target only the new account's auto-created default service and both exact private containers must be account-local"
  }

  assert {
    condition = (
      azurerm_private_endpoint.private_evidence[0].resource_group_name == azurerm_resource_group.control[0].name &&
      azurerm_private_endpoint.private_evidence[0].location == "eastus" &&
      azurerm_private_endpoint.private_evidence[0].subnet_id == azurerm_subnet.private_executor_service_endpoints[0].id &&
      azurerm_private_endpoint.private_evidence[0].subnet_id != azurerm_subnet.marker_private_endpoint[0].id &&
      length(azurerm_private_endpoint.private_evidence[0].private_service_connection) == 1 &&
      azurerm_private_endpoint.private_evidence[0].private_service_connection[0].private_connection_resource_id == azapi_resource.private_evidence_account[0].id &&
      azurerm_private_endpoint.private_evidence[0].private_service_connection[0].subresource_names == tolist(["blob"]) &&
      !azurerm_private_endpoint.private_evidence[0].private_service_connection[0].is_manual_connection &&
      length(azurerm_private_endpoint.private_evidence[0].private_dns_zone_group) == 1 &&
      azurerm_private_endpoint.private_evidence[0].private_dns_zone_group[0].name == "blob" &&
      toset(azurerm_private_endpoint.private_evidence[0].private_dns_zone_group[0].private_dns_zone_ids) == toset([azurerm_private_dns_zone.marker_blob[0].id]) &&
      azurerm_private_dns_zone.marker_blob[0].name == "privatelink.blob.core.windows.net" &&
      azurerm_private_dns_zone_virtual_network_link.marker_blob[0].virtual_network_id == azurerm_virtual_network.control[0].id &&
      !azurerm_private_dns_zone_virtual_network_link.marker_blob[0].registration_enabled
    )
    error_message = "Only the dedicated evidence account Blob endpoint may use the existing control-VNet Blob DNS and service-endpoint subnet"
  }

  assert {
    condition = (
      output.private_evidence_storage_contract.enabled &&
      output.private_evidence_storage_contract.account.resource_id == azapi_resource.private_evidence_account[0].id &&
      output.private_evidence_storage_contract.account.blob_service_uri == "https://cosmaneurarehearsalevd.blob.core.windows.net/" &&
      output.private_evidence_storage_contract.account.blob_service_resource_id == azapi_update_resource.private_evidence_blob_versioning[0].resource_id &&
      output.private_evidence_storage_contract.account.private_endpoint == azurerm_private_endpoint.private_evidence[0].id &&
      output.private_evidence_storage_contract.account.private_dns_zone == azurerm_private_dns_zone.marker_blob[0].id &&
      output.private_evidence_storage_contract.account.private_dns_link == azurerm_private_dns_zone_virtual_network_link.marker_blob[0].id &&
      output.private_evidence_storage_contract.account.linked_vnet == azurerm_virtual_network.control[0].id &&
      output.private_evidence_storage_contract.account.endpoint_subnet == azurerm_subnet.private_executor_service_endpoints[0].id &&
      toset(keys(output.private_evidence_storage_contract.account.containers)) == toset(["restore-evidence-v1", "restore-runtime-bindings-v1"]) &&
      !output.private_evidence_storage_contract.versioning_verified &&
      !output.private_evidence_storage_contract.private_blob_get_proven &&
      !output.private_evidence_storage_contract.job_read_grants_managed &&
      !output.private_evidence_storage_contract.publisher_write_authority_managed &&
      !output.private_evidence_storage_contract.versioned_evidence_binding_complete &&
      !output.private_evidence_storage_contract.locked_runtime_record_binding_complete &&
      !output.private_executor_blocker.implemented &&
      !terraform_data.activation_guard[0].input.private_executor_binding &&
      !terraform_data.activation_guard[0].input.trusted_evidence_binding &&
      output.private_executor_network_contract.private_endpoints &&
      !output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_network_contract.vnet_peering &&
      !output.private_executor_network_contract.public_ingress &&
      !output.private_executor_registry_contract.enabled &&
      !output.private_executor_vault_contract.enabled &&
      !output.target_topology_contract.owned_by_this_root
    )
    error_message = "Empty storage must not claim live versioning, private access, permissions, trusted binding, private compute or target activation"
  }

  assert {
    condition = (
      length(azapi_resource.marker_container) == 7 &&
      toset(keys(azapi_resource.marker_container)) == local.retained_containers &&
      alltrue([for container in azapi_resource.marker_container : container.parent_id == "${azurerm_storage_account.marker[0].id}/blobServices/default"]) &&
      !azurerm_storage_account.marker[0].blob_properties[0].versioning_enabled &&
      length(azurerm_role_assignment.marker_create) == 0 &&
      length(azapi_resource_action.marker_legal_hold) == 0 &&
      length(azurerm_storage_container_immutability_policy.marker) == 0 &&
      length(azurerm_management_lock.marker) == 0
    )
    error_message = "Evidence storage must preserve the seven marker containers and must not enable marker versioning, creation authority or retention controls"
  }
}


override_resource {
  target = azapi_resource.private_launch_mailbox_account
  values = {
    id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/cosmaneurarehearsalmbx"
    output = {
      properties = {
        primaryEndpoints = {
          blob = "https://cosmaneurarehearsalmbx.blob.core.windows.net/"
        }
      }
    }
  }
}

run "mailbox_requires_plan_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = false
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_rejects_approval_without_enable" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = false
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_requires_base" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = false
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_requires_network" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = false
    private_executor_network_plan_approved       = false
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_requires_network_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = false
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_requires_control_cidr" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = false
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_requires_target_cidr" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = false
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_enable_alone_requires_base" {
  command = plan

  variables {
    enable_private_launch_mailbox_storage = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_approval_alone_requires_base" {
  command = plan

  variables {
    private_launch_mailbox_storage_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_rejects_marker_account_name" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
    private_launch_mailbox_storage_account_name  = "cosmaneurarehearsalctl"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_rejects_evidence_account_name" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
    private_launch_mailbox_storage_account_name  = "cosmaneurarehearsalevd"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "mailbox_rejects_invalid_account_name" {
  command = plan

  variables {
    private_launch_mailbox_storage_account_name = "invalid-account"
  }

  expect_failures = [var.private_launch_mailbox_storage_account_name]
}

run "approved_mailbox_is_empty_private_versioned_and_separate" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  assert {
    condition = (
      length(azapi_resource.private_launch_mailbox_account) == 1 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 1 &&
      length(azapi_resource.private_launch_mailbox_container) == 2 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 1 &&
      azapi_resource.private_launch_mailbox_account[0].type == "Microsoft.Storage/storageAccounts@2023-05-01" &&
      azapi_resource.private_launch_mailbox_account[0].name == "cosmaneurarehearsalmbx" &&
      azapi_resource.private_launch_mailbox_account[0].name != azurerm_storage_account.marker[0].name &&
      azapi_resource.private_launch_mailbox_account[0].name != var.private_evidence_storage_account_name &&
      azapi_resource.private_launch_mailbox_account[0].id != azurerm_storage_account.marker[0].id &&
      azapi_resource.private_launch_mailbox_account[0].parent_id == azurerm_resource_group.control[0].id &&
      azapi_resource.private_launch_mailbox_account[0].location == "eastus" &&
      azapi_resource.private_launch_mailbox_account[0].schema_validation_enabled &&
      toset(azapi_resource.private_launch_mailbox_account[0].response_export_values) == toset(["properties.primaryEndpoints.blob"])
    )
    error_message = "Exactly five dedicated control-group managed instances are allowed, with only the non-secret account Blob endpoint exported"
  }

  assert {
    condition = jsonencode(azapi_resource.private_launch_mailbox_account[0].body) == jsonencode({
      kind = "StorageV2"
      sku  = { name = "Standard_ZRS" }
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
    })
    error_message = "The account body must exactly enforce private OAuth-only TLS, encryption and zero network exceptions or auxiliary access paths"
  }

  assert {
    condition = (
      azapi_update_resource.private_launch_mailbox_blob_versioning[0].type == "Microsoft.Storage/storageAccounts/blobServices@2023-05-01" &&
      azapi_update_resource.private_launch_mailbox_blob_versioning[0].resource_id == "${azapi_resource.private_launch_mailbox_account[0].id}/blobServices/default" &&
      jsonencode(azapi_update_resource.private_launch_mailbox_blob_versioning[0].body) == jsonencode({ properties = { isVersioningEnabled = true } }) &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning[0].response_export_values) == 0 &&
      toset(keys(azapi_resource.private_launch_mailbox_container)) == toset(["provision", "cleanup"]) &&
      alltrue([for action, container in azapi_resource.private_launch_mailbox_container : (
        container.name == { provision = "restore-provision-launches-v1", cleanup = "restore-cleanup-launches-v1" }[action] &&
        container.type == "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01" &&
        container.parent_id == "${azapi_resource.private_launch_mailbox_account[0].id}/blobServices/default" &&
        container.parent_id != "${azurerm_storage_account.marker[0].id}/blobServices/default" &&
        jsonencode(container.body) == jsonencode({ properties = { publicAccess = "None" } }) &&
        container.schema_validation_enabled &&
        length(container.response_export_values) == 0
      )])
    )
    error_message = "Versioning must target only the new account's auto-created default service and both exact private containers must be account-local"
  }

  assert {
    condition = (
      azurerm_private_endpoint.private_launch_mailbox[0].resource_group_name == azurerm_resource_group.control[0].name &&
      azurerm_private_endpoint.private_launch_mailbox[0].location == "eastus" &&
      azurerm_private_endpoint.private_launch_mailbox[0].subnet_id == azurerm_subnet.private_executor_service_endpoints[0].id &&
      azurerm_private_endpoint.private_launch_mailbox[0].subnet_id != azurerm_subnet.marker_private_endpoint[0].id &&
      length(azurerm_private_endpoint.private_launch_mailbox[0].private_service_connection) == 1 &&
      azurerm_private_endpoint.private_launch_mailbox[0].private_service_connection[0].private_connection_resource_id == azapi_resource.private_launch_mailbox_account[0].id &&
      azurerm_private_endpoint.private_launch_mailbox[0].private_service_connection[0].subresource_names == tolist(["blob"]) &&
      !azurerm_private_endpoint.private_launch_mailbox[0].private_service_connection[0].is_manual_connection &&
      length(azurerm_private_endpoint.private_launch_mailbox[0].private_dns_zone_group) == 1 &&
      azurerm_private_endpoint.private_launch_mailbox[0].private_dns_zone_group[0].name == "blob" &&
      toset(azurerm_private_endpoint.private_launch_mailbox[0].private_dns_zone_group[0].private_dns_zone_ids) == toset([azurerm_private_dns_zone.marker_blob[0].id]) &&
      azurerm_private_dns_zone.marker_blob[0].name == "privatelink.blob.core.windows.net" &&
      azurerm_private_dns_zone_virtual_network_link.marker_blob[0].virtual_network_id == azurerm_virtual_network.control[0].id &&
      !azurerm_private_dns_zone_virtual_network_link.marker_blob[0].registration_enabled
    )
    error_message = "Only the dedicated mailbox account Blob endpoint may use the existing control-VNet Blob DNS and service-endpoint subnet"
  }

  assert {
    condition = (
      output.private_launch_mailbox_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.account.resource_id == azapi_resource.private_launch_mailbox_account[0].id &&
      output.private_launch_mailbox_storage_contract.account.blob_service_uri == "https://cosmaneurarehearsalmbx.blob.core.windows.net/" &&
      output.private_launch_mailbox_storage_contract.account.blob_service_resource_id == azapi_update_resource.private_launch_mailbox_blob_versioning[0].resource_id &&
      output.private_launch_mailbox_storage_contract.account.private_endpoint == azurerm_private_endpoint.private_launch_mailbox[0].id &&
      output.private_launch_mailbox_storage_contract.account.private_dns_zone == azurerm_private_dns_zone.marker_blob[0].id &&
      output.private_launch_mailbox_storage_contract.account.private_dns_link == azurerm_private_dns_zone_virtual_network_link.marker_blob[0].id &&
      output.private_launch_mailbox_storage_contract.account.linked_vnet == azurerm_virtual_network.control[0].id &&
      output.private_launch_mailbox_storage_contract.account.endpoint_subnet == azurerm_subnet.private_executor_service_endpoints[0].id &&
      toset(keys(output.private_launch_mailbox_storage_contract.account.containers)) == toset(["provision", "cleanup"]) &&
      !output.private_launch_mailbox_storage_contract.blob_versioning_verified &&
      !output.private_launch_mailbox_storage_contract.private_mailbox_read_proven &&
      !output.private_launch_mailbox_storage_contract.job_read_grants_managed &&
      !output.private_launch_mailbox_storage_contract.broker_create_grants_managed &&
      !output.private_launch_mailbox_storage_contract.locked_mailbox_binding_complete &&
      !output.private_launch_mailbox_storage_contract.execution_receipt_handoff_bound &&
      !output.private_executor_blocker.implemented &&
      !terraform_data.activation_guard[0].input.private_executor_binding &&
      !terraform_data.activation_guard[0].input.trusted_evidence_binding &&
      output.private_executor_network_contract.private_endpoints &&
      !output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_network_contract.vnet_peering &&
      !output.private_executor_network_contract.public_ingress &&
      !output.private_executor_registry_contract.enabled &&
      !output.private_executor_vault_contract.enabled &&
      !output.private_evidence_storage_contract.enabled &&
      length(azapi_resource.private_evidence_account) == 0 &&
      !output.target_topology_contract.owned_by_this_root
    )
    error_message = "Empty storage must not claim live versioning, private access, permissions, signed mailbox binding, private compute or target activation"
  }

  assert {
    condition = (
      length(azapi_resource.marker_container) == 7 &&
      toset(keys(azapi_resource.marker_container)) == local.retained_containers &&
      alltrue([for container in azapi_resource.marker_container : container.parent_id == "${azurerm_storage_account.marker[0].id}/blobServices/default"]) &&
      !azurerm_storage_account.marker[0].blob_properties[0].versioning_enabled &&
      length(azurerm_role_assignment.marker_create) == 0 &&
      length(azapi_resource_action.marker_legal_hold) == 0 &&
      length(azurerm_storage_container_immutability_policy.marker) == 0 &&
      length(azurerm_management_lock.marker) == 0
    )
    error_message = "Mailbox storage must preserve the seven marker containers and must not enable marker versioning, creation authority or retention controls"
  }
}

run "approved_private_stores_coexist_without_activation" {
  command = plan

  variables {
    deploy_staging_restore_control_plane         = true
    target_cidr_inventory_revalidated            = true
    control_cidr_inventory_revalidated           = true
    enable_private_executor_network_foundation   = true
    private_executor_network_plan_approved       = true
    enable_private_evidence_storage              = true
    private_evidence_storage_plan_approved       = true
    enable_private_launch_mailbox_storage        = true
    private_launch_mailbox_storage_plan_approved = true
  }

  assert {
    condition = (
      length(azapi_resource.private_evidence_account) == 1 &&
      length(azapi_resource.private_launch_mailbox_account) == 1 &&
      length(azapi_update_resource.private_evidence_blob_versioning) == 1 &&
      length(azapi_update_resource.private_launch_mailbox_blob_versioning) == 1 &&
      length(azapi_resource.private_evidence_container) == 2 &&
      length(azapi_resource.private_launch_mailbox_container) == 2 &&
      length(azurerm_private_endpoint.private_evidence) == 1 &&
      length(azurerm_private_endpoint.private_launch_mailbox) == 1 &&
      azapi_resource.private_evidence_account[0].name != azapi_resource.private_launch_mailbox_account[0].name &&
      azapi_resource.private_evidence_account[0].id != azapi_resource.private_launch_mailbox_account[0].id &&
      toset(keys(azapi_resource.private_evidence_container)) == toset(["restore-evidence-v1", "restore-runtime-bindings-v1"]) &&
      { for action, container in azapi_resource.private_launch_mailbox_container : action => container.name } == { provision = "restore-provision-launches-v1", cleanup = "restore-cleanup-launches-v1" } &&
      alltrue([for container in azapi_resource.private_evidence_container : container.parent_id == "${azapi_resource.private_evidence_account[0].id}/blobServices/default"]) &&
      alltrue([for container in azapi_resource.private_launch_mailbox_container : container.parent_id == "${azapi_resource.private_launch_mailbox_account[0].id}/blobServices/default"])
    )
    error_message = "Both independent approvals must create exactly ten storage instances with distinct accounts and exact account-local/action-keyed containers"
  }

  assert {
    condition = (
      alltrue([for endpoint in [azurerm_private_endpoint.private_evidence[0], azurerm_private_endpoint.private_launch_mailbox[0]] : (
        endpoint.subnet_id == azurerm_subnet.private_executor_service_endpoints[0].id &&
        toset(endpoint.private_dns_zone_group[0].private_dns_zone_ids) == toset([azurerm_private_dns_zone.marker_blob[0].id])
      )]) &&
      azurerm_private_dns_zone_virtual_network_link.marker_blob[0].virtual_network_id == azurerm_virtual_network.control[0].id &&
      !azurerm_private_dns_zone_virtual_network_link.marker_blob[0].registration_enabled &&
      toset(output.private_executor_network_contract.subnets.private_service_endpoints.purposes) == toset([
        "acr", "key-vault", "private-evidence-runtime-record-blob", "private-launch-mailbox-blob", "per-rehearsal-postgresql",
      ])
    )
    error_message = "Both private stores must reuse only the existing control-VNet Blob DNS and service subnet, whose declared purposes must include both Blob paths"
  }

  assert {
    condition = (
      output.private_evidence_storage_contract.enabled &&
      output.private_launch_mailbox_storage_contract.enabled &&
      alltrue([for key in [
        "versioning_verified", "private_blob_get_proven", "job_read_grants_managed",
        "publisher_write_authority_managed", "versioned_evidence_binding_complete", "locked_runtime_record_binding_complete",
      ] : output.private_evidence_storage_contract[key] == false]) &&
      alltrue([for key in [
        "blob_versioning_verified", "private_mailbox_read_proven", "broker_create_grants_managed",
        "job_read_grants_managed", "locked_mailbox_binding_complete", "execution_receipt_handoff_bound",
      ] : output.private_launch_mailbox_storage_contract[key] == false]) &&
      !output.private_executor_blocker.implemented &&
      !terraform_data.activation_guard[0].input.private_executor_binding &&
      !terraform_data.activation_guard[0].input.trusted_evidence_binding &&
      !output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_network_contract.vnet_peering &&
      !output.private_executor_network_contract.public_ingress &&
      !output.target_topology_contract.owned_by_this_root &&
      length(azapi_resource.marker_container) == 7 &&
      length(azurerm_role_assignment.marker_create) == 0 &&
      length(azapi_resource_action.marker_legal_hold) == 0 &&
      length(azurerm_storage_container_immutability_policy.marker) == 0 &&
      length(azurerm_management_lock.marker) == 0
    )
    error_message = "Simultaneous empty storage must not claim live authorization, versioning, private reads, immutable retention, handoff or runtime activation"
  }
}

run "cidr_revalidation_is_required" {
  command = plan

  variables {
    deploy_staging_restore_control_plane = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "marker_create_permissions_are_exact" {
  command = plan

  variables {
    deploy_staging_restore_control_plane  = true
    target_cidr_inventory_revalidated     = true
    control_cidr_inventory_revalidated    = true
    enable_marker_create_assignments      = true
    marker_create_authorization_proven    = true
    private_marker_network_posture_proven = true
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    marker_legal_hold_readback_proven     = true
    lock_marker_immutability_policies     = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  assert {
    condition     = length(azurerm_role_definition.marker_create_and_read) == 1 && length(azurerm_role_assignment.marker_read) == 2 && length(azurerm_role_assignment.marker_create) == 3 && length(azurerm_role_assignment.marker_provision_cleanup) == 1 && toset(keys(azurerm_role_assignment.marker_read)) == toset(["cleanup_intent", "cleanup_submission"]) && toset(keys(azurerm_role_assignment.marker_create)) == toset(["provision_intent", "provision_submission", "cleanup_cleanup"])
    error_message = "The enabled desired state must use one provision cleanup create/read grant and retain exactly two read plus four create assignments"
  }

  assert {
    condition     = length(azurerm_role_assignment.marker_read) + length(azurerm_role_assignment.marker_create) + length(azurerm_role_assignment.marker_provision_cleanup) == 6 && length(output.marker_store_binding.role_assignment_ids) == 6 && output.marker_store_binding.role_assignment_ids.provision_cleanup == azurerm_role_assignment.marker_provision_cleanup[0].id && output.marker_store_binding.private_marker_network_posture_proven && output.marker_store_binding.legal_hold_readback_proven
    error_message = "The activated output must expose six logical assignment IDs with one provision_cleanup grant"
  }

  assert {
    condition     = azurerm_role_assignment.marker_provision_cleanup[0].role_definition_id == azurerm_role_definition.marker_create_and_read[0].role_definition_resource_id
    error_message = "The enabled provision_cleanup assignment must use create/read authority"
  }
}

run "marker_create_requires_private_marker_network_proof" {
  command = plan

  variables {
    deploy_staging_restore_control_plane  = true
    target_cidr_inventory_revalidated     = true
    control_cidr_inventory_revalidated    = true
    enable_marker_create_assignments      = true
    marker_create_authorization_proven    = true
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    marker_legal_hold_readback_proven     = true
    lock_marker_immutability_policies     = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "marker_create_requires_legal_hold_readback" {
  command = plan

  variables {
    deploy_staging_restore_control_plane  = true
    target_cidr_inventory_revalidated     = true
    control_cidr_inventory_revalidated    = true
    enable_marker_create_assignments      = true
    marker_create_authorization_proven    = true
    private_marker_network_posture_proven = true
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    lock_marker_immutability_policies     = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "irreversible_controls_are_independently_gated" {
  command = plan

  variables {
    deploy_staging_restore_control_plane = true
    target_cidr_inventory_revalidated    = true
    control_cidr_inventory_revalidated   = true
    enable_marker_legal_holds            = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "approved_prelock_retention_plan" {
  command = plan

  variables {
    deploy_staging_restore_control_plane  = true
    target_cidr_inventory_revalidated     = true
    control_cidr_inventory_revalidated    = true
    enable_marker_create_assignments      = true
    marker_create_authorization_proven    = true
    private_marker_network_posture_proven = true
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    marker_legal_hold_readback_proven     = true
    lock_marker_immutability_policies     = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  assert {
    condition     = length(azapi_resource_action.marker_legal_hold) == 7 && length(azurerm_storage_container_immutability_policy.marker) == 7
    error_message = "The approved pre-lock retention plan must cover all marker and evidence containers"
  }

  assert {
    condition     = alltrue([for policy in azurerm_storage_container_immutability_policy.marker : policy.locked && policy.immutability_period_in_days == 90 && !policy.protected_append_writes_enabled && !policy.protected_append_writes_all_enabled])
    error_message = "Every marker container must use locked 90-day WORM without protected append writes"
  }

  assert {
    condition     = length(azurerm_management_lock.marker) == 0
    error_message = "ReadOnly must remain absent until the private executor and trusted evidence binding exist"
  }

  assert {
    condition     = alltrue([for id in values(output.marker_store_binding.role_assignment_ids) : id != null]) && length(output.marker_store_binding.immutability_policy_ids) == 3 && output.marker_store_binding.evidence_immutability_policy_id != null
    error_message = "Pre-lock readback outputs must expose six marker assignment IDs, three marker policy IDs, and the evidence policy ID"
  }
}

run "readonly_requires_marker_create_assignments" {
  command = plan

  variables {
    deploy_staging_restore_control_plane  = true
    target_cidr_inventory_revalidated     = true
    control_cidr_inventory_revalidated    = true
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    lock_marker_immutability_policies     = true
    enable_marker_readonly_lock           = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "readonly_remains_blocked_until_final_bindings" {
  command = plan

  variables {
    deploy_staging_restore_control_plane  = true
    target_cidr_inventory_revalidated     = true
    control_cidr_inventory_revalidated    = true
    enable_marker_create_assignments      = true
    marker_create_authorization_proven    = true
    private_marker_network_posture_proven = true
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    marker_legal_hold_readback_proven     = true
    lock_marker_immutability_policies     = true
    enable_marker_readonly_lock           = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  expect_failures = [terraform_data.activation_guard[0]]
}
