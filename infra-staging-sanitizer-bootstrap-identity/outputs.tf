output "state_boundary" {
  description = "Non-secret backend identity; this root shares no state with application or rehearsal roots."
  value = {
    resource_group  = "cosmaneura-tfstate"
    storage_account = "cosmaneuratfstate"
    container       = "cosmaneura-tfstate"
    key             = "staging-sanitizer-bootstrap-identity.tfstate"
  }
}

output "sanitizer_bootstrap_identity_foundation" {
  description = "Non-secret desired-state identity metadata only; no live binding, attachment, federation, role, or database authority is implied."
  value = var.enable_sanitizer_bootstrap_identity ? {
    name                         = azurerm_user_assigned_identity.sanitizer_bootstrap[0].name
    client_id                    = azurerm_user_assigned_identity.sanitizer_bootstrap[0].client_id
    principal_id                 = azurerm_user_assigned_identity.sanitizer_bootstrap[0].principal_id
    resource_id                  = azurerm_user_assigned_identity.sanitizer_bootstrap[0].id
    tenant_id                    = azurerm_user_assigned_identity.sanitizer_bootstrap[0].tenant_id
    lifecycle                    = "ephemeral"
    rehearsal_id                 = var.sanitizer_bootstrap_rehearsal_id
    cleanup_not_after            = var.sanitizer_bootstrap_cleanup_not_after
    federated_credential_ids     = []
    role_assignment_ids          = []
    attached_job_resource_ids    = []
    exact_ids_authenticated      = false
    single_use_job_bound         = false
    provider_authority_bound     = false
    database_authority_bound     = false
    workflow_binding_implemented = false
    destruction_protected        = true
  } : null
}
