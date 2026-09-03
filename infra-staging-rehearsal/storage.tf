resource "azurerm_storage_account" "marker" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                = var.marker_storage_account_name
  resource_group_name = azurerm_resource_group.control[0].name
  location            = local.control_location

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "ZRS"

  allow_nested_items_to_be_public   = false
  cross_tenant_replication_enabled  = false
  default_to_oauth_authentication   = true
  https_traffic_only_enabled        = true
  infrastructure_encryption_enabled = true
  is_hns_enabled                    = false
  local_user_enabled                = false
  min_tls_version                   = "TLS1_2"
  nfsv3_enabled                     = false
  public_network_access_enabled     = false
  shared_access_key_enabled         = false
  sftp_enabled                      = false

  blob_properties {
    change_feed_enabled      = false
    last_access_time_enabled = false
    versioning_enabled       = false
  }

  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    ip_rules                   = []
    virtual_network_subnet_ids = []
  }

  tags = var.tags
}

resource "azapi_resource" "marker_container" {
  for_each = var.deploy_staging_restore_control_plane ? local.retained_containers : toset([])

  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  name      = each.value
  parent_id = "${azurerm_storage_account.marker[0].id}/blobServices/default"
  body = {
    properties = {
      publicAccess = "None"
    }
  }

  # Container creation stays on Azure Resource Manager. It must not depend on
  # public Blob data-plane reachability after the account is made private.
  schema_validation_enabled = true
}

resource "azurerm_private_endpoint" "marker" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                = "cosmaneura-staging-rehearsal-marker-pe"
  resource_group_name = azurerm_resource_group.control[0].name
  location            = azurerm_resource_group.control[0].location
  subnet_id           = azurerm_subnet.marker_private_endpoint[0].id
  tags                = var.tags

  private_service_connection {
    name                           = "cosmaneura-staging-rehearsal-marker-connection"
    private_connection_resource_id = azurerm_storage_account.marker[0].id
    is_manual_connection           = false
    subresource_names              = ["blob"]
  }

  private_dns_zone_group {
    name                 = "blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.marker_blob[0].id]
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.marker_blob]
}

# AzureRM has no first-class legal-hold resource. This explicit management-plane
# action is therefore separately gated and intentionally not described as a
# declarative AzureRM lifecycle. Removing it from state does not clear the hold.
# Pinned AzAPI 2.1.0 performs `when = "apply"` only on resource Create/Update;
# its Read path is state-only. An unchanged later plan therefore does not POST
# setLegalHold again. After ReadOnly is installed, this resource is frozen.
resource "azapi_resource_action" "marker_legal_hold" {
  for_each = (
    var.deploy_staging_restore_control_plane && var.enable_marker_legal_holds
  ) ? azapi_resource.marker_container : {}

  type        = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  resource_id = each.value.id
  action      = "setLegalHold"
  method      = "POST"
  when        = "apply"
  body = {
    tags = ["restorecontrolactive"]
  }

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = var.irreversible_marker_controls_approved
      error_message = "Legal-hold actions require explicit irreversible-marker approval"
    }
  }
}

# locked=true performs Azure's one-way lock operation. prevent_destroy records
# the corresponding source-control invariant; it cannot make Azure reversible.
resource "azurerm_storage_container_immutability_policy" "marker" {
  for_each = (
    var.deploy_staging_restore_control_plane && var.lock_marker_immutability_policies
  ) ? azapi_resource.marker_container : {}

  storage_container_resource_manager_id = each.value.id
  immutability_period_in_days           = 90
  locked                                = true
  protected_append_writes_all_enabled   = false
  protected_append_writes_enabled       = false

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = var.irreversible_marker_controls_approved && var.enable_marker_legal_holds
      error_message = "Locked WORM requires the approved legal-hold phase"
    }
  }

  depends_on = [azapi_resource_action.marker_legal_hold]
}

# This is deliberately last: ReadOnly blocks management-plane changes to the
# account. It must never be enabled in the same casual apply as the base rung.
resource "azurerm_management_lock" "marker" {
  count = (
    var.deploy_staging_restore_control_plane && var.enable_marker_readonly_lock
  ) ? 1 : 0

  name       = "staging-rehearsal-marker-readonly"
  scope      = azurerm_storage_account.marker[0].id
  lock_level = "ReadOnly"
  notes      = "Protect immutable non-PHI restore authorization and cleanup markers."

  lifecycle {
    prevent_destroy = true

    precondition {
      condition = (
        var.irreversible_marker_controls_approved &&
        var.enable_marker_legal_holds &&
        var.lock_marker_immutability_policies &&
        var.enable_marker_create_assignments &&
        local.private_executor_binding_implemented &&
        local.trusted_evidence_binding_implemented
      )
      error_message = "The marker ReadOnly lock requires approved legal holds, locked WORM, proven marker-create assignments, a bound job-only private executor, and a trusted evidence binding"
    }
  }

  depends_on = [
    azapi_resource_action.marker_legal_hold,
    azurerm_role_assignment.marker_create,
    azurerm_storage_container_immutability_policy.marker,
  ]
}
