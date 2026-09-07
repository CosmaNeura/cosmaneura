output "control_plane_enabled" {
  description = "Whether this plan contains the reversible rehearsal control-plane resources."
  value       = var.deploy_staging_restore_control_plane
}

output "state_boundary" {
  description = "Non-secret backend identity; this is distinct from both application roots."
  value = {
    resource_group  = "cosmaneura-tfstate"
    storage_account = "cosmaneuratfstate"
    container       = "cosmaneura-tfstate"
    key             = "staging-rehearsal-control-plane.tfstate"
  }
}

output "target_topology_contract" {
  description = "Ephemeral target topology consumed by the controller; no target VNet is owned by this root."
  value = {
    resource_group                 = local.target_resource_group_name
    location                       = local.target_location
    vnet_address_space             = local.target_vnet_address_space
    private_endpoint_subnet_name   = local.target_private_endpoint_subnet_name
    private_endpoint_subnet_prefix = local.target_private_endpoint_subnet
    owned_by_this_root             = false
  }
}

output "private_executor_blocker" {
  description = "Explicit activation hold: the optional network foundation is not a runner or a route to an ephemeral restore target."
  value = {
    implemented                          = local.private_executor_binding_implemented
    network_foundation_in_source         = local.private_executor_network_foundation_source
    network_foundation_enabled           = var.enable_private_executor_network_foundation
    control_vnet_address_space           = local.control_vnet_address_space
    infrastructure_subnet_address_prefix = local.private_executor_infrastructure_subnet
    service_endpoint_subnet_prefix       = local.private_executor_service_endpoint_subnet
    ephemeral_target_reachability        = "not-implemented"
    container_apps_environment           = "not-implemented"
    private_service_endpoints            = "not-implemented"
    workflow_binding                     = "not-implemented"
    job_identity_binding                 = "not-implemented"
    trusted_evidence_binding             = "not-implemented"
    pre_apply_cidr_revalidation          = var.control_cidr_inventory_revalidated
  }
}

output "private_executor_network_contract" {
  description = "Non-secret exact topology for later live readback; no compute, endpoint, DNS, peering, or ingress is implied."
  value = {
    enabled            = var.enable_private_executor_network_foundation
    location           = local.control_location
    vnet_address_space = local.control_vnet_address_space
    vnet_resource_id = var.deploy_staging_restore_control_plane ? (
      azurerm_virtual_network.control[0].id
    ) : null
    subnets = {
      marker_blob_private_endpoint = {
        address_prefix = local.marker_private_endpoint_subnet
        delegated_to   = null
        resource_id = var.deploy_staging_restore_control_plane ? (
          azurerm_subnet.marker_private_endpoint[0].id
        ) : null
      }
      container_apps_infrastructure = {
        address_prefix = local.private_executor_infrastructure_subnet
        delegated_to = var.enable_private_executor_network_foundation ? (
          "Microsoft.App/environments"
        ) : null
        resource_id = var.deploy_staging_restore_control_plane ? (
          azurerm_subnet.private_executor_reserved[0].id
        ) : null
      }
      private_service_endpoints = {
        address_prefix = local.private_executor_service_endpoint_subnet
        purposes       = ["acr", "key-vault", "per-rehearsal-postgresql"]
        resource_id = (
          var.deploy_staging_restore_control_plane &&
          var.enable_private_executor_network_foundation
        ) ? azurerm_subnet.private_executor_service_endpoints[0].id : null
      }
    }
    vnet_peering               = false
    public_ingress             = false
    route_table                = false
    nat_gateway                = false
    container_apps_environment = false
    private_endpoints          = false
  }
}

output "control_resource_group_id" {
  description = "Persistent non-PHI control RG ID for later exact policy binding."
  value       = var.deploy_staging_restore_control_plane ? azurerm_resource_group.control[0].id : null
}

output "target_resource_group_id" {
  description = "Persistent empty target RG ID; ephemeral restore resources remain controller-owned."
  value       = var.deploy_staging_restore_control_plane ? azurerm_resource_group.target[0].id : null
}

output "restore_identity_bindings" {
  description = "Non-secret dispatcher and unfederated job UAMIs for later exact private-executor reconciliation."
  value = var.deploy_staging_restore_control_plane ? {
    dispatchers = {
      for key, identity in azurerm_user_assigned_identity.dispatcher : key => {
        client_id    = identity.client_id
        principal_id = identity.principal_id
        resource_id  = identity.id
        subject      = azurerm_federated_identity_credential.dispatcher[key].subject
      }
    }
    jobs = {
      for key, identity in azurerm_user_assigned_identity.job : key => {
        client_id                         = identity.client_id
        principal_id                      = identity.principal_id
        resource_id                       = identity.id
        federated_credential              = false
        private_executor_binding_complete = local.private_executor_binding_implemented
      }
    }
  } : {}
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
  } : null
}

output "restore_role_bindings" {
  description = "Deterministic custom-role IDs and canonical permission digests; values require live readback before contract activation."
  value = var.deploy_staging_restore_control_plane ? {
    control_reader = {
      id = azurerm_role_definition.control_reader[0].role_definition_resource_id
      permissions_sha256 = sha256(jsonencode([{
        actions        = sort(local.control_reader_actions)
        notActions     = []
        dataActions    = []
        notDataActions = []
      }]))
    }
    source_reader = {
      id = azurerm_role_definition.source_reader[0].role_definition_resource_id
      permissions_sha256 = sha256(jsonencode([{
        actions        = sort(local.source_reader_actions)
        notActions     = []
        dataActions    = []
        notDataActions = []
      }]))
    }
    target_provisioner = {
      id = azurerm_role_definition.target_provisioner[0].role_definition_resource_id
      permissions_sha256 = sha256(jsonencode([{
        actions        = sort(local.target_provisioner_actions)
        notActions     = []
        dataActions    = []
        notDataActions = []
      }]))
    }
    target_cleanup = {
      id = azurerm_role_definition.target_cleanup[0].role_definition_resource_id
      permissions_sha256 = sha256(jsonencode([{
        actions        = sort(local.target_cleanup_actions)
        notActions     = []
        dataActions    = []
        notDataActions = []
      }]))
    }
    marker_reader = {
      id = azurerm_role_definition.marker_reader[0].role_definition_resource_id
      permissions_sha256 = sha256(jsonencode([{
        actions        = []
        notActions     = []
        dataActions    = sort(local.marker_read_data_actions)
        notDataActions = []
      }]))
    }
    marker_create_and_read = var.enable_marker_create_assignments ? {
      id = azurerm_role_definition.marker_create_and_read[0].role_definition_resource_id
      permissions_sha256 = sha256(jsonencode([{
        actions        = []
        notActions     = []
        dataActions    = sort(tolist(var.marker_create_data_actions))
        notDataActions = []
      }]))
    } : null
  } : null
}

output "deny_policy_binding" {
  description = "Exact deny definition/assignment and canonical rule digest for envelope-policy reconciliation."
  value = var.deploy_staging_restore_control_plane ? {
    definition_id         = azurerm_policy_definition.deny_postgres_firewall_rules[0].id
    assignment_id         = azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0].id
    canonical_rule_sha256 = sha256(jsonencode(local.deny_postgres_firewall_rule))
  } : null
}

output "marker_store_binding" {
  description = "Non-secret marker-store resources. Placeholder replacement still requires live readback and evidence."
  value = var.deploy_staging_restore_control_plane ? {
    storage_account_id          = azurerm_storage_account.marker[0].id
    account_host                = azurerm_storage_account.marker[0].primary_blob_host
    private_endpoint_id         = azurerm_private_endpoint.marker[0].id
    private_dns_zone_id         = azurerm_private_dns_zone.marker_blob[0].id
    private_dns_link_id         = azurerm_private_dns_zone_virtual_network_link.marker_blob[0].id
    reserved_executor_subnet_id = azurerm_subnet.private_executor_reserved[0].id
    container_ids = {
      for key, container in azapi_resource.marker_container : key => container.id
    }
    role_assignment_ids = {
      provision_intent = var.enable_marker_create_assignments ? (
        azurerm_role_assignment.marker_create["provision_intent"].id
      ) : null
      provision_submission = var.enable_marker_create_assignments ? (
        azurerm_role_assignment.marker_create["provision_submission"].id
      ) : null
      provision_cleanup       = azurerm_role_assignment.marker_provision_cleanup[0].id
      cleanup_intent_read     = azurerm_role_assignment.marker_read["cleanup_intent"].id
      cleanup_submission_read = azurerm_role_assignment.marker_read["cleanup_submission"].id
      cleanup_marker = var.enable_marker_create_assignments ? (
        azurerm_role_assignment.marker_create["cleanup_cleanup"].id
      ) : null
    }
    immutability_policy_ids = var.lock_marker_immutability_policies ? {
      intent     = azurerm_storage_container_immutability_policy.marker["restore-intents-v4"].id
      submission = azurerm_storage_container_immutability_policy.marker["restore-submissions-v4"].id
      cleanup    = azurerm_storage_container_immutability_policy.marker["restore-cleanups-v4"].id
    } : {}
    evidence_immutability_policy_id = var.lock_marker_immutability_policies ? (
      azurerm_storage_container_immutability_policy.marker[local.evidence_container_name].id
    ) : null
    legal_hold_requested                  = var.enable_marker_legal_holds
    legal_hold_readback_proven            = var.marker_legal_hold_readback_proven
    locked_worm_requested                 = var.lock_marker_immutability_policies
    private_marker_network_posture_proven = var.private_marker_network_posture_proven
    readonly_lock_requested               = var.enable_marker_readonly_lock
    management_lock_id = var.enable_marker_readonly_lock ? (
      azurerm_management_lock.marker[0].id
    ) : null
  } : null
}
