locals {
  restore_dispatcher_identities = {
    provision = {
      name        = "cosmaneura-staging-restore-provision"
      environment = "staging-restore-provision"
    }
    cleanup = {
      name        = "cosmaneura-staging-restore-cleanup"
      environment = "staging-restore-cleanup"
    }
  }

  # These principals are deliberately not federated. A later private-executor
  # rung must attach them to job compute before any data-plane binding is live.
  restore_job_identities = {
    provision = {
      name = "cosmaneura-staging-restore-provision-job"
    }
    cleanup = {
      name = "cosmaneura-staging-restore-cleanup-job"
    }
  }

  sanitizer_bootstrap_identity_name = "cosmaneura-staging-restore-bootstrap-job"
}

resource "azurerm_user_assigned_identity" "dispatcher" {
  for_each = var.deploy_staging_restore_control_plane ? local.restore_dispatcher_identities : {}

  name                = each.value.name
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  tags                = var.tags
}

# Only dispatcher/control-plane identities trust GitHub. The environments,
# protected-branch policies, and reviewers are intentionally not managed here.
resource "azurerm_federated_identity_credential" "dispatcher" {
  for_each = var.deploy_staging_restore_control_plane ? local.restore_dispatcher_identities : {}

  name                      = "github-${each.value.environment}"
  user_assigned_identity_id = azurerm_user_assigned_identity.dispatcher[each.key].id
  issuer                    = local.oidc_issuer
  subject                   = "repo:${local.github_repository}:environment:${each.value.environment}"
  audience                  = [local.oidc_audience]
}

resource "azurerm_user_assigned_identity" "job" {
  for_each = var.deploy_staging_restore_control_plane ? local.restore_job_identities : {}

  name                = each.value.name
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  tags                = var.tags
}

# This identity is deliberately outside restore_job_identities: it is not a
# persistent runtime identity and receives no role assignment or federation in
# this root. A reviewed rehearsal may create it for the bounded bootstrap
# lifecycle; later slices must authenticate its exact IDs, attach it only to a
# single-use job, and prove all authority and attachment absent before removal.
resource "azurerm_user_assigned_identity" "sanitizer_bootstrap" {
  count = (
    var.deploy_staging_restore_control_plane &&
    var.enable_sanitizer_bootstrap_identity
  ) ? 1 : 0

  name                = local.sanitizer_bootstrap_identity_name
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  tags = merge(var.tags, {
    purpose           = "staging-restore-sanitizer-bootstrap"
    lifecycle         = "ephemeral"
    rehearsal-id      = coalesce(var.sanitizer_bootstrap_rehearsal_id, "unconfigured")
    cleanup-not-after = coalesce(var.sanitizer_bootstrap_cleanup_not_after, "unconfigured")
  })
}
