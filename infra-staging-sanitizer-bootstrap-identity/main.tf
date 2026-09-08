locals {
  subscription_scope          = "/subscriptions/${var.subscription_id}"
  control_resource_group_name = "cosmaneura-staging-rehearsal-control"
  control_location            = "eastus"
  bootstrap_identity_name     = "cosmaneura-staging-restore-bootstrap-job"

  any_bootstrap_input = (
    var.enable_sanitizer_bootstrap_identity ||
    var.sanitizer_bootstrap_identity_plan_approved ||
    var.sanitizer_bootstrap_rehearsal_id != null ||
    var.sanitizer_bootstrap_cleanup_not_after != null
  )
}

# Reading the exact existing group prevents this isolated root from creating or
# owning the shared rehearsal-control group.
data "azurerm_resource_group" "control" {
  count = var.enable_sanitizer_bootstrap_identity ? 1 : 0
  name  = local.control_resource_group_name
}

resource "terraform_data" "activation_guard" {
  count = local.any_bootstrap_input ? 1 : 0

  input = {
    enabled             = var.enable_sanitizer_bootstrap_identity
    exact_plan_approved = var.sanitizer_bootstrap_identity_plan_approved
    rehearsal_id        = var.sanitizer_bootstrap_rehearsal_id
    cleanup_not_after   = var.sanitizer_bootstrap_cleanup_not_after
  }

  lifecycle {
    precondition {
      condition = (
        var.enable_sanitizer_bootstrap_identity ==
        var.sanitizer_bootstrap_identity_plan_approved
      )
      error_message = "The bootstrap identity and exact saved-plan approval must be enabled or disabled together"
    }

    precondition {
      condition = var.enable_sanitizer_bootstrap_identity ? (
        var.sanitizer_bootstrap_rehearsal_id != null &&
        var.sanitizer_bootstrap_cleanup_not_after != null
        ) : (
        var.sanitizer_bootstrap_rehearsal_id == null &&
        var.sanitizer_bootstrap_cleanup_not_after == null
      )
      error_message = "Bootstrap lifecycle metadata must be present exactly when the identity is enabled"
    }

    precondition {
      condition = var.enable_sanitizer_bootstrap_identity ? try(
        timecmp(var.sanitizer_bootstrap_cleanup_not_after, plantimestamp()) > 0,
        false,
      ) : true
      error_message = "The bootstrap identity cleanup deadline must be later than the exact plan timestamp"
    }
  }
}

# This is the only Azure resource in this state. It receives no federation,
# role assignment, job attachment, provider authority, or database authority.
resource "azurerm_user_assigned_identity" "sanitizer_bootstrap" {
  count = var.enable_sanitizer_bootstrap_identity ? 1 : 0

  name                = local.bootstrap_identity_name
  resource_group_name = data.azurerm_resource_group.control[0].name
  location            = data.azurerm_resource_group.control[0].location
  tags = merge(var.tags, {
    lifecycle         = "ephemeral"
    rehearsal-id      = coalesce(var.sanitizer_bootstrap_rehearsal_id, "unconfigured")
    cleanup-not-after = coalesce(var.sanitizer_bootstrap_cleanup_not_after, "unconfigured")
  })

  depends_on = [terraform_data.activation_guard]

  lifecycle {
    # A later teardown slice must first authenticate that every temporary
    # attachment and authority is absent, then deliberately change this guard.
    prevent_destroy = true

    precondition {
      condition = lower(data.azurerm_resource_group.control[0].id) == lower(
        "${local.subscription_scope}/resourceGroups/${local.control_resource_group_name}"
      )
      error_message = "The bootstrap UAMI may use only the exact staging rehearsal control resource group"
    }

    precondition {
      condition     = lower(data.azurerm_resource_group.control[0].location) == local.control_location
      error_message = "The existing staging rehearsal control resource group must remain in East US"
    }

    # timestamp() is unknown during plan and is reevaluated immediately before
    # this resource is created, so an expired saved plan cannot create the UAMI.
    precondition {
      condition = try(
        timecmp(var.sanitizer_bootstrap_cleanup_not_after, timestamp()) > 0,
        false,
      )
      error_message = "A saved bootstrap identity plan cannot be applied after its cleanup deadline"
    }
  }
}
