locals {
  private_broker_identities_enabled = (
    var.deploy_staging_restore_control_plane &&
    var.enable_private_restore_broker_identities
  )
  restore_broker_identities = {
    provision = { name = "cosmaneura-staging-restore-provision-broker" }
    cleanup   = { name = "cosmaneura-staging-restore-cleanup-broker" }
  }
}

# Unfederated and unattached identity objects only. Future broker authority and
# compute must be reviewed and bound separately; these objects grant neither.
resource "azurerm_user_assigned_identity" "private_broker" {
  for_each = local.private_broker_identities_enabled ? local.restore_broker_identities : {}

  name                = each.value.name
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  tags                = var.tags

  lifecycle {
    prevent_destroy = true
  }
}
