output "python_containers" {
  value = [
    azurerm_container_app.app.name,
    azurerm_container_app.worker.name
  ]
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "scribe_workflow_managed_identity_client_id" {
  description = "Non-secret client ID assigned Scribe.Workflow.Access in Entra."
  value       = azurerm_user_assigned_identity.scribe_workflow.client_id
}

output "scribe_workflow_managed_identity_principal_id" {
  description = "Object ID used when assigning the Scribe API application role."
  value       = azurerm_user_assigned_identity.scribe_workflow.principal_id
}

output "deid_managed_identity_client_id" {
  description = "Non-secret client ID pinned by the production worker for Azure DeID calls."
  value       = azurerm_user_assigned_identity.health_services.client_id
}

output "deid_managed_identity_principal_id" {
  description = "Object ID holding the realtime-only role at the AnonymizerAPI resource."
  value       = azurerm_user_assigned_identity.health_services.principal_id
}

output "parallel_platform_virtual_network_id" {
  description = "Non-secret VNet ID supplied to the separately reviewed Scribe connectivity plan."
  value       = var.deploy_parallel_vnet_environment ? azurerm_virtual_network.parallel_platform[0].id : null
}

output "parallel_platform_container_apps_environment_id" {
  description = "ID of the empty parallel Container Apps environment; no workload or traffic is implied."
  value       = var.deploy_parallel_vnet_environment ? azurerm_container_app_environment.parallel_platform[0].id : null
}

output "parallel_platform_container_apps_default_domain" {
  description = "Azure-generated domain for later synthetic validation; no custom domain is attached."
  value       = var.deploy_parallel_vnet_environment ? azurerm_container_app_environment.parallel_platform[0].default_domain : null
}

output "parallel_postgres_private_endpoint_id" {
  description = "Private bridge to the existing PostgreSQL server; public access is not disabled by this resource."
  value       = local.parallel_private_data_enabled ? azurerm_private_endpoint.parallel_postgres[0].id : null
}

output "parallel_redis_private_endpoint_id" {
  description = "Private bridge to the retiring Azure Cache for Redis instance; migration remains separate."
  value       = local.parallel_private_data_enabled ? azurerm_private_endpoint.parallel_redis[0].id : null
}

output "parallel_uploads_blob_private_endpoint_id" {
  description = "Private bridge to the existing uploads Blob endpoint; shared-key retirement remains separate."
  value       = local.parallel_private_data_enabled ? azurerm_private_endpoint.parallel_uploads_blob[0].id : null
}

output "parallel_network_smoke_job_name" {
  description = "Manual synthetic job name; null unless the separately approved job definition is deployed."
  value       = local.parallel_network_smoke_enabled ? azurerm_container_app_job.parallel_network_smoke[0].name : null
}

# output "acr_admin_password" {
#   value     = azurerm_container_registry.main.admin_password
#   sensitive = true
# }
