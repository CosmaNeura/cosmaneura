locals {
  control_reader_actions = [
    "Microsoft.Authorization/policyAssignments/read",
    "Microsoft.Authorization/policyDefinitions/read",
    "Microsoft.Authorization/policyExemptions/read",
    "Microsoft.Authorization/roleAssignments/read",
    "Microsoft.Authorization/roleDefinitions/read",
    "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/read",
    "Microsoft.ManagedIdentity/userAssignedIdentities/read",
    "Microsoft.Resources/subscriptions/read",
    "Microsoft.Resources/subscriptions/resourceGroups/read",
    "Microsoft.Resources/subscriptions/resourcegroups/resources/read",
  ]

  source_reader_actions = [
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/read",
  ]

  target_provisioner_actions = [
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/privateEndpointConnections/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/privateEndpointConnectionsApproval/action",
    "Microsoft.DBforPostgreSQL/flexibleServers/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/write",
    "Microsoft.Network/networkInterfaces/read",
    "Microsoft.Network/privateDnsZones/read",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/read",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/write",
    "Microsoft.Network/privateDnsZones/write",
    "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/read",
    "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/write",
    "Microsoft.Network/privateEndpoints/read",
    "Microsoft.Network/privateEndpoints/write",
    "Microsoft.Network/publicIPAddresses/read",
    "Microsoft.Network/virtualNetworks/read",
    "Microsoft.Network/virtualNetworks/subnets/join/action",
    "Microsoft.Network/virtualNetworks/subnets/read",
    "Microsoft.Network/virtualNetworks/subnets/write",
    "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/read",
    "Microsoft.Network/virtualNetworks/write",
    "Microsoft.Resources/tags/write",
  ]

  target_cleanup_actions = [
    "Microsoft.DBforPostgreSQL/flexibleServers/delete",
    "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/privateEndpointConnections/read",
    "Microsoft.DBforPostgreSQL/flexibleServers/read",
    "Microsoft.Network/networkInterfaces/delete",
    "Microsoft.Network/networkInterfaces/read",
    "Microsoft.Network/privateDnsZones/delete",
    "Microsoft.Network/privateDnsZones/read",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/delete",
    "Microsoft.Network/privateDnsZones/virtualNetworkLinks/read",
    "Microsoft.Network/privateEndpoints/delete",
    "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/delete",
    "Microsoft.Network/privateEndpoints/privateDnsZoneGroups/read",
    "Microsoft.Network/privateEndpoints/read",
    "Microsoft.Network/publicIPAddresses/read",
    "Microsoft.Network/virtualNetworks/delete",
    "Microsoft.Network/virtualNetworks/read",
    "Microsoft.Network/virtualNetworks/subnets/delete",
    "Microsoft.Network/virtualNetworks/subnets/read",
    "Microsoft.Network/virtualNetworks/virtualNetworkPeerings/read",
    "Microsoft.Resources/tags/read",
  ]

  marker_read_data_actions = [
    "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
  ]
}

resource "azurerm_role_definition" "control_reader" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  role_definition_id = local.role_definition_ids.control_reader
  name               = "Cosmaneura staging restore control reader"
  scope              = local.subscription_scope
  description        = "Read only the subscription metadata required by the staging restore preflight."

  permissions {
    actions          = local.control_reader_actions
    not_actions      = []
    data_actions     = []
    not_data_actions = []
  }

  assignable_scopes = [local.subscription_scope]
}

resource "azurerm_role_definition" "source_reader" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  role_definition_id = local.role_definition_ids.source_reader
  name               = "Cosmaneura staging restore source reader"
  scope              = local.subscription_scope
  description        = "Read only the exact staging PostgreSQL source and its firewall rules."

  permissions {
    actions          = local.source_reader_actions
    not_actions      = []
    data_actions     = []
    not_data_actions = []
  }

  assignable_scopes = [local.subscription_scope]
}

resource "azurerm_role_definition" "target_provisioner" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  role_definition_id = local.role_definition_ids.target_provisioner
  name               = "Cosmaneura staging restore target provisioner"
  scope              = local.subscription_scope
  description        = "Create and isolate one approved PostgreSQL restore inside the dedicated target RG."

  permissions {
    actions          = local.target_provisioner_actions
    not_actions      = []
    data_actions     = []
    not_data_actions = []
  }

  assignable_scopes = [local.subscription_scope]
}

resource "azurerm_role_definition" "target_cleanup" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  role_definition_id = local.role_definition_ids.target_cleanup
  name               = "Cosmaneura staging restore target cleanup"
  scope              = local.subscription_scope
  description        = "Read and delete only approved restore-envelope resource kinds in the target RG."

  permissions {
    actions          = local.target_cleanup_actions
    not_actions      = []
    data_actions     = []
    not_data_actions = []
  }

  assignable_scopes = [local.subscription_scope]
}

resource "azurerm_role_definition" "marker_reader" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  role_definition_id = local.role_definition_ids.marker_reader
  name               = "Cosmaneura staging restore marker reader"
  scope              = local.subscription_scope
  description        = "Read immutable restore markers from an explicitly assigned container only."

  permissions {
    actions          = []
    not_actions      = []
    data_actions     = local.marker_read_data_actions
    not_data_actions = []
  }

  assignable_scopes = [local.subscription_scope]
}

# There is intentionally no default create permission. The exact minimum for
# conditional BlockBlob Put must be proven live first; Azure may require write
# rather than add. The role and its assignments stay absent until that proof.
resource "azurerm_role_definition" "marker_create_and_read" {
  count = (
    var.deploy_staging_restore_control_plane && var.enable_marker_create_assignments
  ) ? 1 : 0

  role_definition_id = local.role_definition_ids.marker_create_and_read
  name               = "Cosmaneura staging restore marker create and read"
  scope              = local.subscription_scope
  description        = "Create and read immutable restore markers in an explicitly assigned container."

  permissions {
    actions          = []
    not_actions      = []
    data_actions     = sort(tolist(var.marker_create_data_actions))
    not_data_actions = []
  }

  assignable_scopes = [local.subscription_scope]

  lifecycle {
    precondition {
      condition     = var.enable_marker_create_assignments
      error_message = "The marker create role must remain absent until exact data actions are proven live"
    }
  }
}

resource "azurerm_role_assignment" "control_reader" {
  for_each = var.deploy_staging_restore_control_plane ? local.restore_dispatcher_identities : {}

  name                             = uuidv5("url", "${local.subscription_scope}|${each.value.name}|control-reader")
  scope                            = local.subscription_scope
  role_definition_id               = azurerm_role_definition.control_reader[0].role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.dispatcher[each.key].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "source_reader" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                             = uuidv5("url", "${local.source_postgres_resource_id}|provision|source-reader")
  scope                            = local.source_postgres_resource_id
  role_definition_id               = azurerm_role_definition.source_reader[0].role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.job["provision"].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "target_provisioner" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                             = uuidv5("url", "${azurerm_resource_group.target[0].id}|provision|target-provisioner")
  scope                            = azurerm_resource_group.target[0].id
  role_definition_id               = azurerm_role_definition.target_provisioner[0].role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.job["provision"].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "target_cleanup" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name                             = uuidv5("url", "${azurerm_resource_group.target[0].id}|cleanup|target-cleanup")
  scope                            = azurerm_resource_group.target[0].id
  role_definition_id               = azurerm_role_definition.target_cleanup[0].role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.job["cleanup"].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

locals {
  marker_read_assignments = {
    provision_cleanup = {
      identity  = "provision"
      container = "restore-cleanups-v4"
    }
    cleanup_intent = {
      identity  = "cleanup"
      container = "restore-intents-v4"
    }
    cleanup_submission = {
      identity  = "cleanup"
      container = "restore-submissions-v4"
    }
  }

  marker_create_assignments = {
    provision_intent = {
      identity  = "provision"
      container = "restore-intents-v4"
    }
    provision_submission = {
      identity  = "provision"
      container = "restore-submissions-v4"
    }
    cleanup_cleanup = {
      identity  = "cleanup"
      container = "restore-cleanups-v4"
    }
  }
}

resource "azurerm_role_assignment" "marker_read" {
  for_each = var.deploy_staging_restore_control_plane ? local.marker_read_assignments : {}

  name                             = uuidv5("url", "${local.github_repository}|${each.key}|marker-read")
  scope                            = azapi_resource.marker_container[each.value.container].id
  role_definition_id               = azurerm_role_definition.marker_reader[0].role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.job[each.value.identity].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "marker_create" {
  for_each = (
    var.deploy_staging_restore_control_plane && var.enable_marker_create_assignments
  ) ? local.marker_create_assignments : {}

  name                             = uuidv5("url", "${local.github_repository}|${each.key}|marker-create-read")
  scope                            = azapi_resource.marker_container[each.value.container].id
  role_definition_id               = azurerm_role_definition.marker_create_and_read[0].role_definition_resource_id
  principal_id                     = azurerm_user_assigned_identity.job[each.value.identity].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true

  depends_on = [
    azapi_resource_action.marker_legal_hold,
    azurerm_storage_container_immutability_policy.marker,
  ]
}
