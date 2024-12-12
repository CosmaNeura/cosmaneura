output "redis_credentials" {
  sensitive = true

  value = {
    host       = azurerm_redis_cache.main.hostname
    port       = azurerm_redis_cache.main.ssl_port
    access_key = azurerm_redis_cache.main.primary_access_key
  }
}

output "redis_url" {
  sensitive = true
  # the extra `s` is really important! When `enable_non_ssl_port = false` non-secure access will not work
  value = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:${azurerm_redis_cache.main.ssl_port}?ssl_cert_reqs=optional"
}

output "postgres_credentials" {
  sensitive = true
  value     = "postgresql://${azurerm_postgresql_flexible_server.main.administrator_login}:${azurerm_postgresql_flexible_server.main.administrator_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/production?sslmode=prefer"
}

# output "metabase_url" {
#   value = "https://${azurerm_container_group.cosmaneura-app.ip_address}"
# }
