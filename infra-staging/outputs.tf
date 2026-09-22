output "resource_group" {
  value = azurerm_resource_group.main.name
}

# output "acr_admin_password" {
#   value     = azurerm_container_registry.main.admin_password
#   sensitive = true
# }
