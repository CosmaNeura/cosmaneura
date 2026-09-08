locals {
  approved_subscription_id          = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
  approved_tenant_id                = "d51754d0-bba5-4810-820e-afac542844d6"
  subscription_scope                = "/subscriptions/${local.approved_subscription_id}"
  control_resource_group_name       = "cosmaneura-staging-rehearsal-control"
  control_location                  = "eastus"
  bootstrap_identity_name           = "cosmaneura-staging-restore-bootstrap-job"
  bootstrap_identity_resource_id    = "${local.subscription_scope}/resourceGroups/${local.control_resource_group_name}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${local.bootstrap_identity_name}"
  bootstrap_rehearsal_id            = "21506aaeec3c"
  bootstrap_cleanup_not_after       = "2026-09-08T17:40:01Z"
  approved_teardown_evidence_sha256 = "sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
  base_tags = {
    environment         = "staging"
    managed-by          = "opentofu"
    purpose             = "staging-restore-sanitizer-bootstrap"
    data-classification = "non-phi-control-metadata"
  }

}

# This root is now teardown-only. Keeping both the data source and managed
# identity at count zero makes recreation impossible from this source.
data "azurerm_resource_group" "control" {
  count = 0
  name  = local.control_resource_group_name
}

resource "terraform_data" "activation_guard" {
  # Keep an explicit state tombstone. A default plan fails closed instead of
  # silently planning the identity deletion.
  count = 1

  input = {
    mode                     = "teardown-only"
    teardown_authorized      = var.authorize_sanitizer_bootstrap_identity_teardown
    rehearsal_id             = var.sanitizer_bootstrap_teardown_rehearsal_id
    teardown_evidence_sha256 = var.sanitizer_bootstrap_teardown_evidence_sha256
    identity_resource_id     = local.bootstrap_identity_resource_id
    cleanup_not_after        = local.bootstrap_cleanup_not_after
  }

  triggers_replace = {
    # Preserve the exact creation-time values already stored at serial 2 so
    # the guard updates in place instead of being replaced during teardown.
    rehearsal_id      = local.bootstrap_rehearsal_id
    lifetime_hours    = 6
    cleanup_not_after = local.bootstrap_cleanup_not_after
  }

  lifecycle {
    # A blanket `tofu destroy` must remain impossible. The reviewed normal plan
    # removes only the count-zero UAMI and preserves this state tombstone.
    prevent_destroy = true

    precondition {
      condition = (
        var.enable_sanitizer_bootstrap_identity == false &&
        var.sanitizer_bootstrap_identity_plan_approved == false &&
        var.sanitizer_bootstrap_rehearsal_id == null &&
        var.sanitizer_bootstrap_lifetime_hours == null
      )
      error_message = "This source is teardown-only and cannot create, replace, or extend a bootstrap identity"
    }

    precondition {
      condition = var.authorize_sanitizer_bootstrap_identity_teardown ? (
        var.sanitizer_bootstrap_teardown_rehearsal_id == local.bootstrap_rehearsal_id &&
        var.sanitizer_bootstrap_teardown_evidence_sha256 == local.approved_teardown_evidence_sha256
        ) : (
        var.sanitizer_bootstrap_teardown_rehearsal_id == null &&
        var.sanitizer_bootstrap_teardown_evidence_sha256 == null
      )
      error_message = "Teardown requires the exact reviewed rehearsal and live-readback evidence digest"
    }

    precondition {
      condition     = var.authorize_sanitizer_bootstrap_identity_teardown
      error_message = "The default plan is blocked; explicitly authorize the exact reviewed teardown"
    }
  }
}

# The prior source created exactly this UAMI. Count zero is intentional and
# one-way: a separately reviewed source change is required before any future
# bootstrap identity can be created.
resource "azurerm_user_assigned_identity" "sanitizer_bootstrap" {
  count = 0

  # Keep every targeted plan coupled to the teardown authorization guard. This
  # prevents `-target` from bypassing the exact rehearsal/evidence checks.
  depends_on = [terraform_data.activation_guard]

  name                = local.bootstrap_identity_name
  resource_group_name = data.azurerm_resource_group.control[0].name
  location            = data.azurerm_resource_group.control[0].location
  tags = merge(local.base_tags, {
    lifecycle         = "ephemeral"
    rehearsal-id      = coalesce(var.sanitizer_bootstrap_rehearsal_id, "unconfigured")
    cleanup-not-after = coalesce(local.bootstrap_cleanup_not_after, "unconfigured")
  })

}
