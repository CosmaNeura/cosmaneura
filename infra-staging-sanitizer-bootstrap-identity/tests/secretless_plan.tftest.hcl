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

run "default_plan_refuses_without_explicit_teardown" {
  command = plan

  expect_failures = [terraform_data.activation_guard[0]]
}

run "creation_controls_are_permanently_disabled" {
  command = plan

  variables {
    enable_sanitizer_bootstrap_identity        = true
    sanitizer_bootstrap_identity_plan_approved = true
    sanitizer_bootstrap_rehearsal_id           = "abcdef123456"
    sanitizer_bootstrap_lifetime_hours         = 6
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "teardown_requires_complete_binding" {
  command = plan

  variables {
    authorize_sanitizer_bootstrap_identity_teardown = true
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "teardown_rejects_wrong_rehearsal" {
  command = plan

  variables {
    authorize_sanitizer_bootstrap_identity_teardown = true
    sanitizer_bootstrap_teardown_rehearsal_id       = "abcdef123456"
    sanitizer_bootstrap_teardown_evidence_sha256    = "sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "teardown_rejects_wrong_evidence" {
  command = plan

  variables {
    authorize_sanitizer_bootstrap_identity_teardown = true
    sanitizer_bootstrap_teardown_rehearsal_id       = "21506aaeec3c"
    sanitizer_bootstrap_teardown_evidence_sha256    = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }

  expect_failures = [terraform_data.activation_guard[0]]
}

run "teardown_rejects_malformed_evidence_digest" {
  command = plan

  variables {
    sanitizer_bootstrap_teardown_evidence_sha256 = "2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
  }

  expect_failures = [var.sanitizer_bootstrap_teardown_evidence_sha256]
}

run "exact_reviewed_teardown_is_source_level_one_way" {
  command = plan

  variables {
    authorize_sanitizer_bootstrap_identity_teardown = true
    sanitizer_bootstrap_teardown_rehearsal_id       = "21506aaeec3c"
    sanitizer_bootstrap_teardown_evidence_sha256    = "sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
  }

  assert {
    condition     = length(terraform_data.activation_guard) == 1 && length(data.azurerm_resource_group.control) == 0 && length(azurerm_user_assigned_identity.sanitizer_bootstrap) == 0
    error_message = "The exact teardown plan must retain one tombstone and contain no data read or creatable Azure identity"
  }

  assert {
    condition     = terraform_data.activation_guard[0].input.mode == "teardown-only" && terraform_data.activation_guard[0].input.teardown_authorized && terraform_data.activation_guard[0].input.rehearsal_id == "21506aaeec3c" && terraform_data.activation_guard[0].input.teardown_evidence_sha256 == "sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
    error_message = "The teardown tombstone must bind the exact independently reviewed evidence"
  }

  assert {
    condition     = terraform_data.activation_guard[0].triggers_replace.rehearsal_id == "21506aaeec3c" && terraform_data.activation_guard[0].triggers_replace.lifetime_hours == 6 && terraform_data.activation_guard[0].triggers_replace.cleanup_not_after == "2026-09-08T17:40:01Z"
    error_message = "The guard must preserve its exact serial-2 replacement triggers and update in place"
  }

  assert {
    condition     = output.sanitizer_bootstrap_identity_foundation == null && output.sanitizer_bootstrap_identity_teardown.identity_resource_id == "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-staging-rehearsal-control/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-staging-restore-bootstrap-job" && output.sanitizer_bootstrap_identity_teardown.founder_apply_required && !output.sanitizer_bootstrap_identity_teardown.apply_authorized
    error_message = "The output must remain a non-authoritative exact teardown tombstone"
  }
}
