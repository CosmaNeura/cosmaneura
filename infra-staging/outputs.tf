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

# output "acr_admin_password" {
#   value     = azurerm_container_registry.main.admin_password
#   sensitive = true
# }
