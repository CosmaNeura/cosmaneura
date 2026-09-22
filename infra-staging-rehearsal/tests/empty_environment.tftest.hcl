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


# Independent new file so local changed-only checks need not replay old tests.
run "empty_environment_default_absent" {
  command = plan
  assert {
    condition     = length(azurerm_container_app_environment.private_executor) == 0 && !output.private_executor_environment_contract.enabled && output.private_executor_environment_contract.environment == null && length(terraform_data.activation_guard) == 0
    error_message = "The environment and activation guard must be absent by default"
  }
}
run "network_alone_does_not_create_environment" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
  }
  assert {
    condition     = length(azurerm_container_app_environment.private_executor) == 0 && !output.private_executor_network_contract.container_apps_environment
    error_message = "Network approval is not environment creation authority"
  }
}
run "environment_requires_plan_approval" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_environment        = true
  }
  expect_failures = [terraform_data.activation_guard]
}
run "environment_approval_requires_enable" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    private_executor_environment_plan_approved = true
  }
  expect_failures = [terraform_data.activation_guard]
}
run "environment_requires_network" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = false
    private_executor_network_plan_approved     = false
    enable_private_executor_environment        = true
    private_executor_environment_plan_approved = true
  }
  expect_failures = [terraform_data.activation_guard]
}
run "environment_requires_control_plane" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = false
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_environment        = true
    private_executor_environment_plan_approved = true
  }
  expect_failures = [terraform_data.activation_guard]
}
run "environment_requires_fresh_control_cidr" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = false
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_environment        = true
    private_executor_environment_plan_approved = true
  }
  expect_failures = [terraform_data.activation_guard]
}
run "environment_requires_fresh_target_cidr" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = false
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_environment        = true
    private_executor_environment_plan_approved = true
  }
  expect_failures = [terraform_data.activation_guard]
}
run "approved_environment_is_empty_internal_and_non_authorizing" {
  command = plan
  variables {
    deploy_staging_restore_control_plane       = true
    target_cidr_inventory_revalidated          = true
    control_cidr_inventory_revalidated         = true
    enable_private_executor_network_foundation = true
    private_executor_network_plan_approved     = true
    enable_private_executor_environment        = true
    private_executor_environment_plan_approved = true
  }
  assert {
    condition = (
      length(azurerm_container_app_environment.private_executor) == 1 &&
      azurerm_container_app_environment.private_executor[0].name == "cosmaneura-staging-restore-executor" &&
      azurerm_container_app_environment.private_executor[0].internal_load_balancer_enabled &&
      azurerm_container_app_environment.private_executor[0].public_network_access == "Disabled" &&
      azurerm_container_app_environment.private_executor[0].infrastructure_resource_group_name == "cosmaneura-staging-rehearsal-executor-infra" &&
      length(azurerm_container_app_environment.private_executor[0].identity) == 0 &&
      !azurerm_container_app_environment.private_executor[0].zone_redundancy_enabled
    )
    error_message = "Only one empty, internal, identity-free environment is permitted"
  }
  assert {
    condition = (
      length(azurerm_container_app_environment.private_executor[0].workload_profile) == 1 &&
      one(azurerm_container_app_environment.private_executor[0].workload_profile).name == "Consumption" &&
      one(azurerm_container_app_environment.private_executor[0].workload_profile).workload_profile_type == "Consumption" &&
      azurerm_container_app_environment.private_executor[0].infrastructure_subnet_id == azurerm_subnet.private_executor_reserved[0].id &&
      one(azurerm_subnet.private_executor_reserved[0].address_prefixes) == "10.76.0.32/27" &&
      !azurerm_subnet.private_executor_reserved[0].default_outbound_access_enabled
    )
    error_message = "Workload-profiles must use the exact dedicated /27, not a service endpoint or legacy subnet"
  }
  assert {
    condition = (
      output.private_executor_environment_contract.enabled &&
      output.private_executor_network_contract.container_apps_environment &&
      !output.private_executor_blocker.implemented &&
      !output.private_executor_environment_contract.runtime_binding_complete &&
      !output.private_executor_environment_contract.job_identity_attachments_managed &&
      !output.private_executor_environment_contract.jobs_managed &&
      !output.private_executor_environment_contract.images_published &&
      !output.private_executor_environment_contract.evidence_binding_complete &&
      !output.private_executor_environment_contract.explicit_workload_egress_proven &&
      !output.private_executor_environment_contract.offline_verifier_containment_proven &&
      !output.private_executor_environment_contract.private_reachability_proven &&
      !output.private_executor_environment_contract.workflow_integrated
    )
    error_message = "Environment creation must not claim runtime, identity, egress, evidence or deployment acceptance"
  }
  assert {
    condition = (
      length(azurerm_container_registry.private_executor) == 0 &&
      length(azapi_resource.private_executor_vault) == 0 &&
      length(azapi_resource.private_evidence_account) == 0 &&
      length(azapi_resource.private_launch_mailbox_account) == 0 &&
      length(azurerm_user_assigned_identity.private_broker) == 0 &&
      !output.private_broker_identity_contract.compute_attachments_managed
    )
    error_message = "Environment enablement must not imply separate service, broker or grant approvals"
  }
}
