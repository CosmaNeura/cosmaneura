locals {
  # Suppress subnet indexing for a partial request; the activation guard still
  # rejects it. An empty environment is not an executable restore binding.
  private_executor_environment_enabled = (
    var.deploy_staging_restore_control_plane &&
    var.enable_private_executor_network_foundation &&
    var.enable_private_executor_environment
  )
}

# Empty internal workload-profiles environment only. No apps, jobs, identities,
# images, credentials, app/job ingress configuration, logs sink or data-plane grants.
resource "azurerm_container_app_environment" "private_executor" {
  count = local.private_executor_environment_enabled ? 1 : 0

  name                               = "cosmaneura-staging-restore-executor"
  resource_group_name                = azurerm_resource_group.control[0].name
  location                           = azurerm_resource_group.control[0].location
  infrastructure_resource_group_name = "cosmaneura-staging-rehearsal-executor-infra"
  infrastructure_subnet_id           = azurerm_subnet.private_executor_reserved[0].id
  internal_load_balancer_enabled     = true
  public_network_access              = "Disabled"
  zone_redundancy_enabled            = false
  tags                               = var.tags

  # Explicitly select workload-profiles, not legacy Consumption-only. The
  # existing dedicated /27 is supported only by workload-profiles environments.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  lifecycle {
    # Creation flags are not rollback. Preserve an environment after later jobs
    # or evidence become attached; removal needs a separately reviewed plan.
    prevent_destroy = true
  }

  depends_on = [terraform_data.activation_guard]
}

output "private_executor_environment_contract" {
  description = "Planned empty environment only; no live private-network, workload, identity, egress or runtime proof."
  value = {
    enabled                             = local.private_executor_environment_enabled
    runtime_binding_complete            = false
    job_identity_attachments_managed    = false
    jobs_managed                        = false
    images_published                    = false
    evidence_binding_complete           = false
    explicit_workload_egress_proven     = false
    offline_verifier_containment_proven = false
    private_reachability_proven         = false
    workflow_integrated                 = false
    environment = local.private_executor_environment_enabled ? {
      resource_id                       = azurerm_container_app_environment.private_executor[0].id
      name                              = azurerm_container_app_environment.private_executor[0].name
      location                          = azurerm_container_app_environment.private_executor[0].location
      infrastructure_subnet_resource_id = azurerm_subnet.private_executor_reserved[0].id
      infrastructure_subnet_cidr        = local.private_executor_infrastructure_subnet
      infrastructure_resource_group     = azurerm_container_app_environment.private_executor[0].infrastructure_resource_group_name
      internal_only                     = azurerm_container_app_environment.private_executor[0].internal_load_balancer_enabled
      public_network_access             = azurerm_container_app_environment.private_executor[0].public_network_access
      workload_profile                  = "Consumption"
      subnet_default_outbound_access    = azurerm_subnet.private_executor_reserved[0].default_outbound_access_enabled
    } : null
  }
}
