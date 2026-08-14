output "python_containers" {
  value = [
    azurerm_container_app.app.name,
    azurerm_container_app.worker.name,
    azurerm_container_app.job-monitor.name
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

# output "acr_admin_password" {
#   value     = azurerm_container_registry.main.admin_password
#   sensitive = true
# }
