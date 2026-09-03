mock_provider "azapi" {
  mock_resource "azapi_resource" {
    defaults = {
      id = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mock/blobServices/default/containers/mock"
    }
  }
}

mock_provider "azurerm" {
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

run "default_is_dormant" {
  command = plan

  assert {
    condition     = length(terraform_data.activation_guard) == 0 && length(terraform_data.private_executor_topology_blocker) == 0
    error_message = "The default plan must not create Terraform activation resources"
  }

  assert {
    condition     = length(azurerm_resource_group.control) == 0 && length(azurerm_resource_group.target) == 0
    error_message = "The default plan must not create Azure resource groups"
  }

  assert {
    condition     = length(azurerm_virtual_network.control) == 0 && length(azurerm_subnet.marker_private_endpoint) == 0 && length(azurerm_subnet.private_executor_reserved) == 0 && length(azurerm_private_dns_zone.marker_blob) == 0 && length(azurerm_private_dns_zone_virtual_network_link.marker_blob) == 0 && length(azurerm_private_endpoint.marker) == 0
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
    condition     = length(azurerm_role_assignment.control_reader) == 0 && length(azurerm_role_assignment.source_reader) == 0 && length(azurerm_role_assignment.target_provisioner) == 0 && length(azurerm_role_assignment.target_cleanup) == 0 && length(azurerm_role_assignment.marker_read) == 0 && length(azurerm_role_assignment.marker_create) == 0
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
}

run "reversible_control_plane" {
  command = plan

  variables {
    deploy_staging_restore_control_plane = true
    target_cidr_inventory_revalidated    = true
    control_cidr_inventory_revalidated   = true
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
    condition     = length(azurerm_virtual_network.control[0].address_space) == 1 && contains(azurerm_virtual_network.control[0].address_space, "10.76.0.0/24") && length(azurerm_subnet.private_executor_reserved[0].address_prefixes) == 1 && azurerm_subnet.private_executor_reserved[0].address_prefixes[0] == "10.76.0.32/27"
    error_message = "The persistent control network and reserved executor subnet must remain distinct from the target"
  }

  assert {
    condition     = output.private_executor_blocker.implemented == false && output.private_executor_blocker.ephemeral_target_reachability == "not-implemented"
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
    condition     = toset(keys(azapi_resource.marker_container)) == toset(["restore-intents-v3", "restore-submissions-v3", "restore-cleanups-v3", "restore-evidence-v1"])
    error_message = "All three marker containers and the reserved evidence container must be managed through Azure Resource Manager"
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
    condition     = output.marker_store_binding.role_assignment_ids.provision_intent == null && output.marker_store_binding.role_assignment_ids.provision_submission == null && output.marker_store_binding.role_assignment_ids.cleanup_marker == null && length(output.marker_store_binding.immutability_policy_ids) == 0 && output.marker_store_binding.evidence_immutability_policy_id == null
    error_message = "Create-role and immutability IDs must remain null or empty while their gates are off"
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
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    lock_marker_immutability_policies     = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  assert {
    condition     = length(azurerm_role_definition.marker_create_and_read) == 1 && length(azurerm_role_assignment.marker_create) == 3
    error_message = "Live-proven marker create/read permissions must be assigned at exactly three container scopes"
  }
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
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    lock_marker_immutability_policies     = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  assert {
    condition     = length(azapi_resource_action.marker_legal_hold) == 4 && length(azurerm_storage_container_immutability_policy.marker) == 4
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
    irreversible_marker_controls_approved = true
    enable_marker_legal_holds             = true
    lock_marker_immutability_policies     = true
    enable_marker_readonly_lock           = true
    marker_create_data_actions = [
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
      "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
    ]
  }

  expect_failures = [terraform_data.activation_guard[0]]
}
