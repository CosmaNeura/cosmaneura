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
    condition     = length(azurerm_virtual_network.control) == 0 && length(azurerm_subnet.marker_private_endpoint) == 0 && length(azurerm_subnet.private_executor_reserved) == 0 && length(azurerm_subnet.private_executor_service_endpoints) == 0 && length(azurerm_private_dns_zone.marker_blob) == 0 && length(azurerm_private_dns_zone_virtual_network_link.marker_blob) == 0 && length(azurerm_private_endpoint.marker) == 0
    error_message = "The default plan must not create network or private-link resources"
  }

  assert {
    condition     = length(azurerm_storage_account.marker) == 0 && length(azapi_resource.marker_container) == 0
    error_message = "The default plan must not create the marker or evidence store"
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.dispatcher) == 0 && length(azurerm_user_assigned_identity.job) == 0 && length(azurerm_user_assigned_identity.sanitizer_bootstrap) == 0 && length(azurerm_federated_identity_credential.dispatcher) == 0
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
    condition     = output.marker_store_binding == null && length(output.restore_identity_bindings) == 0 && output.sanitizer_bootstrap_identity_foundation == null
    error_message = "Default-off outputs must not imply live marker or identity bindings"
  }

  assert {
    condition     = !output.private_executor_network_contract.enabled && output.private_executor_network_contract.vnet_resource_id == null && output.private_executor_network_contract.subnets.private_service_endpoints.resource_id == null
    error_message = "The default outputs must not imply an enabled or deployed private-executor network"
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
    condition     = azurerm_federated_identity_credential.dispatcher["provision"].subject == "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision" && azurerm_federated_identity_credential.dispatcher["cleanup"].subject == "repo:CosmaNeura/cosmaneura:environment:staging-restore-cleanup" && length(azurerm_user_assigned_identity.job) == 2 && length(azurerm_user_assigned_identity.sanitizer_bootstrap) == 0 && output.sanitizer_bootstrap_identity_foundation == null
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

run "bootstrap_identity_requires_exact_plan_approval" {
  command = plan

  variables {
    deploy_staging_restore_control_plane = true
    target_cidr_inventory_revalidated    = true
    control_cidr_inventory_revalidated   = true
    enable_sanitizer_bootstrap_identity  = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "bootstrap_identity_approval_cannot_be_stale" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    sanitizer_bootstrap_identity_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "bootstrap_identity_requires_lifecycle_metadata" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "approved_bootstrap_identity_foundation" {
  command = plan

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
    sanitizer_bootstrap_rehearsal_id           = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after      = "2026-09-08T04:00:00Z"
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.sanitizer_bootstrap) == 1 && azurerm_user_assigned_identity.sanitizer_bootstrap[0].name == "cosmaneura-staging-restore-bootstrap-job" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].resource_group_name == azurerm_resource_group.control[0].name && azurerm_user_assigned_identity.sanitizer_bootstrap[0].location == "eastus" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].tags["rehearsal-id"] == "abcdef123456" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].tags["cleanup-not-after"] == "2026-09-08T04:00:00Z"
    error_message = "The approved rung must create exactly the named East US bootstrap UAMI in the isolated control group"
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.job) == 2 && length(azurerm_federated_identity_credential.dispatcher) == 2 && length(azurerm_role_assignment.control_reader) == 2 && length(azurerm_role_assignment.source_reader) == 1 && length(azurerm_role_assignment.target_provisioner) == 1 && length(azurerm_role_assignment.target_cleanup) == 1 && length(azurerm_role_assignment.marker_read) == 2 && length(azurerm_role_assignment.marker_provision_cleanup) == 1 && length(azurerm_role_assignment.marker_create) == 0
    error_message = "Creating the bootstrap identity must not widen federation, runtime job identities, or any role-assignment set"
  }

  assert {
    condition     = output.sanitizer_bootstrap_identity_foundation.name == "cosmaneura-staging-restore-bootstrap-job" && output.sanitizer_bootstrap_identity_foundation.lifecycle == "ephemeral" && output.sanitizer_bootstrap_identity_foundation.rehearsal_id == "abcdef123456" && output.sanitizer_bootstrap_identity_foundation.cleanup_not_after == "2026-09-08T04:00:00Z" && length(output.sanitizer_bootstrap_identity_foundation.federated_credential_ids) == 0 && length(output.sanitizer_bootstrap_identity_foundation.role_assignment_ids) == 0 && length(output.sanitizer_bootstrap_identity_foundation.attached_job_resource_ids) == 0 && !output.sanitizer_bootstrap_identity_foundation.exact_ids_authenticated && !output.sanitizer_bootstrap_identity_foundation.single_use_job_bound && !output.sanitizer_bootstrap_identity_foundation.provider_authority_bound && !output.sanitizer_bootstrap_identity_foundation.database_authority_bound && !output.sanitizer_bootstrap_identity_foundation.workflow_binding_implemented
    error_message = "The bootstrap output must remain explicit non-authoritative desired-state metadata"
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

  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
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
