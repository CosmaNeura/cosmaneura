variable "enable_scribe_database_entra_auth" {
  description = "Add Microsoft Entra authentication to the existing production PostgreSQL server while retaining password authentication. This does not authorize a migration or Scribe activation."
  type        = bool
  default     = false
}

variable "enable_scribe_database_migration_administrator" {
  description = "Temporarily register the exact Scribe-owned migration identity as PostgreSQL Entra administrator. Disable after least-privilege database roles are provisioned and independently verified."
  type        = bool
  default     = false

  validation {
    condition = (
      !var.enable_scribe_database_migration_administrator ||
      var.enable_scribe_database_entra_auth
    )
    error_message = "the Scribe database migration administrator requires additive PostgreSQL Entra authentication"
  }
}

variable "scribe_database_migration_identity_binding" {
  description = "Exact non-secret identifiers of the Scribe-owned production migration UAMI. Populating this object is not deployment or migration authority."
  type = object({
    resource_id  = string
    client_id    = string
    principal_id = string
  })
  default = null

  validation {
    condition = (
      !var.enable_scribe_database_migration_administrator ||
      try(
        lower(var.scribe_database_migration_identity_binding.resource_id) == lower(
          "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-scribe-production/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-scribe-production-database-migrations"
        ) &&
        can(regex("^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", lower(var.scribe_database_migration_identity_binding.client_id))) &&
        lower(var.scribe_database_migration_identity_binding.client_id) != "00000000-0000-0000-0000-000000000000" &&
        can(regex("^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", lower(var.scribe_database_migration_identity_binding.principal_id))) &&
        lower(var.scribe_database_migration_identity_binding.principal_id) != "00000000-0000-0000-0000-000000000000",
        false,
      )
    )
    error_message = "the migration administrator requires the exact Scribe production migration UAMI resource ID and nonzero client/principal UUIDs"
  }
}

locals {
  scribe_database_migration_identity_parts = split(
    "/",
    trimprefix(try(var.scribe_database_migration_identity_binding.resource_id, ""), "/"),
  )
  scribe_database_migration_identity_resource_group = try(
    local.scribe_database_migration_identity_parts[3],
    "disabled",
  )
  scribe_database_migration_identity_name = try(
    local.scribe_database_migration_identity_parts[7],
    "disabled",
  )
}

data "azurerm_user_assigned_identity" "scribe_database_migrations" {
  count = var.enable_scribe_database_migration_administrator ? 1 : 0

  name                = local.scribe_database_migration_identity_name
  resource_group_name = local.scribe_database_migration_identity_resource_group

  lifecycle {
    postcondition {
      condition = (
        lower(self.id) == lower(var.scribe_database_migration_identity_binding.resource_id) &&
        lower(self.client_id) == lower(var.scribe_database_migration_identity_binding.client_id) &&
        lower(self.principal_id) == lower(var.scribe_database_migration_identity_binding.principal_id)
      )
      error_message = "the live Scribe migration identity does not match the exact reviewed resource/client/principal binding"
    }
  }
}

resource "azurerm_postgresql_flexible_server_active_directory_administrator" "scribe_database_migrations" {
  count = var.enable_scribe_database_migration_administrator ? 1 : 0

  server_name         = azurerm_postgresql_flexible_server.main.name
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = local.azure_tenant_id
  object_id           = data.azurerm_user_assigned_identity.scribe_database_migrations[0].principal_id
  principal_name      = data.azurerm_user_assigned_identity.scribe_database_migrations[0].name
  principal_type      = "ServicePrincipal"

  lifecycle {
    precondition {
      condition     = azurerm_postgresql_flexible_server.main.authentication[0].active_directory_auth_enabled
      error_message = "PostgreSQL Entra authentication must be enabled before the Scribe migration administrator is registered"
    }
  }
}
