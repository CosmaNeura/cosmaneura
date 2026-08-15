locals {
  parallel_private_data_enabled = (
    var.deploy_parallel_vnet_environment &&
    var.deploy_parallel_private_data_endpoints
  )
  parallel_source_postgres_resource_id = "${azurerm_resource_group.main.id}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${local.base_name}-postgres"
  parallel_source_redis_resource_id    = "${azurerm_resource_group.main.id}/providers/Microsoft.Cache/Redis/${local.base_name}-redis"
  parallel_source_uploads_resource_id  = "${azurerm_resource_group.main.id}/providers/Microsoft.Storage/storageAccounts/${local.base_name_alphanumeric}uploads"
}

# Keep a failed dependency explicit instead of silently planning zero data-path resources.
resource "terraform_data" "parallel_private_data_guard" {
  count = var.deploy_parallel_private_data_endpoints ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.deploy_parallel_vnet_environment
      error_message = "deploy_parallel_private_data_endpoints requires deploy_parallel_vnet_environment=true"
    }
  }
}

resource "azurerm_subnet" "parallel_private_endpoints" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                 = "private-endpoints"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.parallel_platform[0].name
  address_prefixes     = var.parallel_platform_private_endpoint_subnet_address_prefixes

  private_endpoint_network_policies = "Disabled"

  depends_on = [terraform_data.parallel_private_data_guard]
}

resource "azurerm_private_dns_zone" "parallel_postgres" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                = "privatelink.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.main.name

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_dns_zone_virtual_network_link" "parallel_postgres" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                  = "${local.base_name}-parallel-postgres-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.parallel_postgres[0].name
  virtual_network_id    = azurerm_virtual_network.parallel_platform[0].id
  registration_enabled  = false

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_endpoint" "parallel_postgres" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                = "${local.base_name}-parallel-postgres-pe"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.parallel_platform_location
  subnet_id           = azurerm_subnet.parallel_private_endpoints[0].id

  private_service_connection {
    name                           = "${local.base_name}-parallel-postgres-connection"
    private_connection_resource_id = local.parallel_source_postgres_resource_id
    is_manual_connection           = false
    subresource_names              = ["postgresqlServer"]
  }

  private_dns_zone_group {
    name                 = "postgres"
    private_dns_zone_ids = [azurerm_private_dns_zone.parallel_postgres[0].id]
  }

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_dns_zone" "parallel_redis" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                = "privatelink.redis.cache.windows.net"
  resource_group_name = azurerm_resource_group.main.name

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_dns_zone_virtual_network_link" "parallel_redis" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                  = "${local.base_name}-parallel-redis-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.parallel_redis[0].name
  virtual_network_id    = azurerm_virtual_network.parallel_platform[0].id
  registration_enabled  = false

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_endpoint" "parallel_redis" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                = "${local.base_name}-parallel-redis-pe"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.parallel_platform_location
  subnet_id           = azurerm_subnet.parallel_private_endpoints[0].id

  private_service_connection {
    name                           = "${local.base_name}-parallel-redis-connection"
    private_connection_resource_id = local.parallel_source_redis_resource_id
    is_manual_connection           = false
    subresource_names              = ["redisCache"]
  }

  private_dns_zone_group {
    name                 = "redis"
    private_dns_zone_ids = [azurerm_private_dns_zone.parallel_redis[0].id]
  }

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_dns_zone" "parallel_uploads_blob" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.main.name

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_dns_zone_virtual_network_link" "parallel_uploads_blob" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                  = "${local.base_name}-parallel-uploads-link"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.parallel_uploads_blob[0].name
  virtual_network_id    = azurerm_virtual_network.parallel_platform[0].id
  registration_enabled  = false

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}

resource "azurerm_private_endpoint" "parallel_uploads_blob" {
  count = local.parallel_private_data_enabled ? 1 : 0

  name                = "${local.base_name}-parallel-uploads-blob-pe"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.parallel_platform_location
  subnet_id           = azurerm_subnet.parallel_private_endpoints[0].id

  private_service_connection {
    name                           = "${local.base_name}-parallel-uploads-connection"
    private_connection_resource_id = local.parallel_source_uploads_resource_id
    is_manual_connection           = false
    subresource_names              = ["blob"]
  }

  private_dns_zone_group {
    name                 = "blob"
    private_dns_zone_ids = [azurerm_private_dns_zone.parallel_uploads_blob[0].id]
  }

  tags = merge(var.tags, {
    purpose = "parallel-platform-migration"
  })
}
