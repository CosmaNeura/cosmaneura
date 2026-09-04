locals {
  subscription_scope = "/subscriptions/${var.subscription_id}"

  control_resource_group_name = "cosmaneura-staging-rehearsal-control"
  target_resource_group_name  = "cosmaneura-staging-rehearsal"
  control_location            = "eastus"
  target_location             = "northcentralus"

  # The target envelope is created and removed per rehearsal by the controller.
  # These values are contract-only here so the persistent target RG remains empty.
  target_vnet_address_space           = "10.75.0.0/24"
  target_private_endpoint_subnet      = "10.75.0.0/27"
  target_private_endpoint_subnet_name = "private-endpoints"

  # The marker store and a future private executor use a distinct persistent
  # control network. The network-foundation flag delegates the reserved
  # infrastructure subnet and adds the private-service-endpoint subnet; it does
  # not create a runner, endpoint, route, or ephemeral-target connection.
  control_vnet_address_space                 = "10.76.0.0/24"
  marker_private_endpoint_subnet             = "10.76.0.0/27"
  private_executor_infrastructure_subnet     = "10.76.0.32/27"
  private_executor_service_endpoint_subnet   = "10.76.0.64/27"
  private_executor_network_foundation_source = true
  private_executor_binding_implemented       = false
  trusted_evidence_binding_implemented       = false

  source_postgres_resource_id = "${local.subscription_scope}/resourceGroups/cosmaneura-staging/providers/Microsoft.DBforPostgreSQL/flexibleServers/cosmaneura-staging-postgres"

  marker_containers = toset([
    # Retain the dormant v3 namespace so a later apply can never destroy or
    # reinterpret an immutable marker written outside the current model.
    "restore-intents-v3",
    "restore-submissions-v3",
    "restore-cleanups-v3",
    "restore-intents-v4",
    "restore-submissions-v4",
    "restore-cleanups-v4",
  ])
  evidence_container_name = "restore-evidence-v1"
  retained_containers = setunion(
    local.marker_containers,
    toset([local.evidence_container_name]),
  )

  github_repository = "CosmaNeura/cosmaneura"
  oidc_issuer       = "https://token.actions.githubusercontent.com"
  oidc_audience     = "api://AzureADTokenExchange"

  # Stable UUIDv5 IDs make the role resources and downstream policy binding
  # deterministic without querying or mutating Azure during review.
  role_definition_ids = {
    control_reader         = uuidv5("url", "https://github.com/CosmaNeura/cosmaneura/roles/staging-restore-control-reader-v1")
    source_reader          = uuidv5("url", "https://github.com/CosmaNeura/cosmaneura/roles/staging-restore-source-reader-v1")
    target_provisioner     = uuidv5("url", "https://github.com/CosmaNeura/cosmaneura/roles/staging-restore-target-provisioner-v1")
    target_cleanup         = uuidv5("url", "https://github.com/CosmaNeura/cosmaneura/roles/staging-restore-target-cleanup-v1")
    marker_reader          = uuidv5("url", "https://github.com/CosmaNeura/cosmaneura/roles/staging-restore-marker-reader-v1")
    marker_create_and_read = uuidv5("url", "https://github.com/CosmaNeura/cosmaneura/roles/staging-restore-marker-create-read-v1")
  }

  irreversible_controls_requested = (
    var.enable_marker_legal_holds ||
    var.lock_marker_immutability_policies ||
    var.enable_marker_readonly_lock
  )
  any_control_requested = (
    var.deploy_staging_restore_control_plane ||
    var.enable_private_executor_network_foundation ||
    var.private_executor_network_plan_approved ||
    var.enable_marker_create_assignments ||
    local.irreversible_controls_requested
  )
}

resource "terraform_data" "activation_guard" {
  count = local.any_control_requested ? 1 : 0

  input = {
    deploy_control_plane                = var.deploy_staging_restore_control_plane
    target_cidr_inventory_revalidated   = var.target_cidr_inventory_revalidated
    control_cidr_inventory_revalidated  = var.control_cidr_inventory_revalidated
    private_executor_network_foundation = var.enable_private_executor_network_foundation
    private_executor_network_approved   = var.private_executor_network_plan_approved
    marker_create_assignments           = var.enable_marker_create_assignments
    marker_create_authorization_proven  = var.marker_create_authorization_proven
    irreversible_controls_approved      = var.irreversible_marker_controls_approved
    marker_legal_holds                  = var.enable_marker_legal_holds
    locked_marker_immutability_policies = var.lock_marker_immutability_policies
    marker_readonly_lock                = var.enable_marker_readonly_lock
    private_executor_binding            = local.private_executor_binding_implemented
    trusted_evidence_binding            = local.trusted_evidence_binding_implemented
  }

  lifecycle {
    precondition {
      condition     = var.deploy_staging_restore_control_plane
      error_message = "Every control-plane or retention option requires deploy_staging_restore_control_plane=true"
    }

    precondition {
      condition     = var.target_cidr_inventory_revalidated && var.control_cidr_inventory_revalidated
      error_message = "Revalidate both 10.75.0.0/24 and 10.76.0.0/24 against the complete connected-network inventory immediately before an apply"
    }

    precondition {
      condition = (
        var.enable_private_executor_network_foundation ==
        var.private_executor_network_plan_approved
      )
      error_message = "The private executor network foundation and its exact saved-plan approval must be enabled or disabled together"
    }

    precondition {
      condition = (
        !var.enable_marker_create_assignments ||
        (
          var.marker_create_authorization_proven &&
          contains(var.marker_create_data_actions, "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read") &&
          length(setintersection(var.marker_create_data_actions, toset([
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
          ]))) == 1 &&
          length(var.marker_create_data_actions) == 2
        )
      )
      error_message = "Marker create assignments require exactly read plus one live-proven create action (add or write)"
    }


    precondition {
      condition = (
        !var.enable_marker_create_assignments ||
        (
          var.irreversible_marker_controls_approved &&
          var.enable_marker_legal_holds &&
          var.lock_marker_immutability_policies
        )
      )
      error_message = "Runtime marker-create assignments require the approved legal-hold and locked-WORM phase"
    }

    precondition {
      condition     = !local.irreversible_controls_requested || var.irreversible_marker_controls_approved
      error_message = "Locked WORM, legal hold, and ReadOnly-lock changes require explicit action-time irreversible_marker_controls_approved=true"
    }

    precondition {
      condition     = !var.lock_marker_immutability_policies || var.enable_marker_legal_holds
      error_message = "Locking WORM policies requires the legal-hold action in the same reviewed phase"
    }

    precondition {
      condition = (
        !var.enable_marker_readonly_lock ||
        (
          var.enable_marker_legal_holds &&
          var.lock_marker_immutability_policies &&
          var.enable_marker_create_assignments
        )
      )
      error_message = "The ReadOnly management lock is last: legal holds, locked WORM policies, and all three proven marker-create assignments must already be enabled"
    }

    precondition {
      condition = (
        !var.enable_marker_readonly_lock ||
        (
          local.private_executor_binding_implemented &&
          local.trusted_evidence_binding_implemented
        )
      )
      error_message = "The ReadOnly management lock remains blocked until the private executor uses job-only identities and the trusted evidence sink is bound"
    }
  }
}

resource "azurerm_resource_group" "control" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name     = local.control_resource_group_name
  location = local.control_location
  tags     = var.tags

  depends_on = [terraform_data.activation_guard]
}

resource "azurerm_resource_group" "target" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  name     = local.target_resource_group_name
  location = local.target_location
  tags = merge(var.tags, {
    purpose = "staging-restore-rehearsal-ephemeral-target"
  })

  depends_on = [terraform_data.activation_guard]
}

resource "terraform_data" "private_executor_topology_blocker" {
  count = var.deploy_staging_restore_control_plane ? 1 : 0

  input = {
    implemented                   = local.private_executor_binding_implemented
    network_foundation_in_source  = local.private_executor_network_foundation_source
    network_foundation_enabled    = var.enable_private_executor_network_foundation
    infrastructure_subnet         = local.private_executor_infrastructure_subnet
    service_endpoint_subnet       = local.private_executor_service_endpoint_subnet
    ephemeral_target_reachability = "not-implemented"
    workflow_binding              = "not-implemented"
  }

  depends_on = [azurerm_resource_group.control, azurerm_resource_group.target]
}
