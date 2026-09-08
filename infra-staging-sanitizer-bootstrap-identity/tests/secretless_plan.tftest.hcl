mock_provider "azurerm" {
  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-staging-rehearsal-control"
      location = "eastus"
      tags     = {}
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      client_id    = "22222222-2222-4222-8222-222222222222"
      id           = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-staging-rehearsal-control/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-staging-restore-bootstrap-job"
      principal_id = "33333333-3333-4333-8333-333333333333"
      tenant_id    = "d51754d0-bba5-4810-820e-afac542844d6"
    }
  }
}

run "default_is_dormant" {
  command = plan

  assert {
    condition     = length(terraform_data.activation_guard) == 0 && length(data.azurerm_resource_group.control) == 0 && length(azurerm_user_assigned_identity.sanitizer_bootstrap) == 0
    error_message = "The default plan must not read or create any bootstrap resource"
  }

  assert {
    condition     = output.sanitizer_bootstrap_identity_foundation == null && output.state_boundary.key == "staging-sanitizer-bootstrap-identity.tfstate"
    error_message = "The dormant output must expose only the isolated state boundary"
  }
}

run "identity_requires_exact_plan_approval" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity   = true
    sanitizer_bootstrap_rehearsal_id      = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after = "2099-01-01T00:00:00Z"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "approval_requires_enable_switch" {
  command = plan

  variables {
    sanitizer_bootstrap_identity_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "identity_rejects_invalid_cleanup_timestamp" {
  command = plan

  variables {
    sanitizer_bootstrap_cleanup_not_after = "2026-99-99T99:99:99Z"
  }

  expect_failures = [var.sanitizer_bootstrap_cleanup_not_after]
}

run "identity_requires_lifecycle_metadata" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "dormant_root_rejects_lifecycle_metadata" {
  command = plan

  variables {
    sanitizer_bootstrap_rehearsal_id      = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after = "2099-01-01T00:00:00Z"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "identity_rejects_expired_cleanup_deadline" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
    sanitizer_bootstrap_rehearsal_id           = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after      = "2020-01-01T00:00:00Z"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "identity_rejects_wrong_control_group_location" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
    sanitizer_bootstrap_rehearsal_id           = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after      = "2099-01-01T00:00:00Z"
  }

  override_data {
    target = data.azurerm_resource_group.control
    values = {
      id       = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-staging-rehearsal-control"
      location = "westus"
      tags     = {}
    }
  }

  expect_failures = [azurerm_user_assigned_identity.sanitizer_bootstrap[0]]
}

run "identity_rejects_wrong_subscription_scope" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
    sanitizer_bootstrap_rehearsal_id           = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after      = "2099-01-01T00:00:00Z"
  }

  override_data {
    target = data.azurerm_resource_group.control
    values = {
      id       = "/subscriptions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/resourceGroups/cosmaneura-staging-rehearsal-control"
      location = "eastus"
      tags     = {}
    }
  }

  expect_failures = [azurerm_user_assigned_identity.sanitizer_bootstrap[0]]
}

run "approved_identity_foundation" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
    sanitizer_bootstrap_rehearsal_id           = "abcdef123456"
    sanitizer_bootstrap_cleanup_not_after      = "2099-01-01T00:00:00Z"
  }

  assert {
    condition     = length(terraform_data.activation_guard) == 1 && length(data.azurerm_resource_group.control) == 1 && length(azurerm_user_assigned_identity.sanitizer_bootstrap) == 1
    error_message = "The approved root must contain one guard, one exact resource-group read, and one Azure resource"
  }

  assert {
    condition     = azurerm_user_assigned_identity.sanitizer_bootstrap[0].name == "cosmaneura-staging-restore-bootstrap-job" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].resource_group_name == "cosmaneura-staging-rehearsal-control" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].location == "eastus" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].tags["rehearsal-id"] == "abcdef123456" && azurerm_user_assigned_identity.sanitizer_bootstrap[0].tags["cleanup-not-after"] == "2099-01-01T00:00:00Z"
    error_message = "The approved plan must create exactly the named East US UAMI with bounded orphan-detection tags"
  }

  assert {
    condition     = output.sanitizer_bootstrap_identity_foundation.name == "cosmaneura-staging-restore-bootstrap-job" && output.sanitizer_bootstrap_identity_foundation.lifecycle == "ephemeral" && output.sanitizer_bootstrap_identity_foundation.rehearsal_id == "abcdef123456" && output.sanitizer_bootstrap_identity_foundation.cleanup_not_after == "2099-01-01T00:00:00Z" && length(output.sanitizer_bootstrap_identity_foundation.federated_credential_ids) == 0 && length(output.sanitizer_bootstrap_identity_foundation.role_assignment_ids) == 0 && length(output.sanitizer_bootstrap_identity_foundation.attached_job_resource_ids) == 0 && !output.sanitizer_bootstrap_identity_foundation.exact_ids_authenticated && !output.sanitizer_bootstrap_identity_foundation.single_use_job_bound && !output.sanitizer_bootstrap_identity_foundation.provider_authority_bound && !output.sanitizer_bootstrap_identity_foundation.database_authority_bound && !output.sanitizer_bootstrap_identity_foundation.workflow_binding_implemented && output.sanitizer_bootstrap_identity_foundation.destruction_protected
    error_message = "The output must remain explicit non-authoritative, destruction-protected desired-state metadata"
  }
}
