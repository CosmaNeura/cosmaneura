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
  description = "Creation is permanently disabled in this teardown-only source."
  value       = null
}

output "sanitizer_bootstrap_identity_teardown" {
  description = "Non-secret teardown tombstone; it grants no Azure mutation authority."
  value = var.authorize_sanitizer_bootstrap_identity_teardown ? {
    mode                     = "teardown-only"
    identity_resource_id     = local.bootstrap_identity_resource_id
    rehearsal_id             = var.sanitizer_bootstrap_teardown_rehearsal_id
    cleanup_not_after        = local.bootstrap_cleanup_not_after
    teardown_evidence_sha256 = var.sanitizer_bootstrap_teardown_evidence_sha256
    founder_apply_required   = true
    apply_authorized         = false
  } : null
}
