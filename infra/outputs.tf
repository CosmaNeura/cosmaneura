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

# output "acr_admin_password" {
#   value     = azurerm_container_registry.main.admin_password
#   sensitive = true
# }
