# Public-PHI observability is intentionally default-off. Enable first in
# staging with a role mailbox, prove synthetic alert delivery and response, and
# attach the evidence before proposing the separately reviewed production plan.
module "public_phi_observability" {
  count  = var.enable_public_phi_observability ? 1 : 0
  source = "./modules/public_phi_observability"

  environment                = local.environment
  resource_prefix            = local.base_name
  resource_group_id          = azurerm_resource_group.main.id
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  postgresql_server_id       = azurerm_postgresql_flexible_server.main.id
  redis_cache_id             = azurerm_redis_cache.main.id
  upload_storage_account_id  = azurerm_storage_account.uploads.id
  alert_email                = var.public_phi_alert_email
  support_owner              = var.public_phi_support_owner
  tags                       = var.tags
}
