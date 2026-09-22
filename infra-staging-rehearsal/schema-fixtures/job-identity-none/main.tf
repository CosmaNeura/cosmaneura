# Synthetic schema fixture ONLY. Never plan or apply this directory.
# The checker copies it to an isolated temporary directory, initializes without
# a backend and runs provider schema validation, with all authentication disabled.
terraform {
  required_version = "= 1.12.5"
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "= 2.4.0"
    }
    # Preserve the full root lockfile dependency set without trimming hashes.
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.73.0"
    }
  }
}

provider "azapi" {
  subscription_id  = "00000000-0000-4000-8000-000000000001"
  tenant_id        = "00000000-0000-4000-8000-000000000002"
  enable_preflight = false
  use_cli          = false
  use_msi          = false
  use_oidc         = false
}

# No AzureRM resource is present; authentication remains disabled nevertheless.
provider "azurerm" {
  features {}
  subscription_id                 = "00000000-0000-4000-8000-000000000001"
  tenant_id                       = "00000000-0000-4000-8000-000000000002"
  resource_provider_registrations = "none"
  use_cli                         = false
  use_msi                         = false
  use_oidc                        = false
}

resource "azapi_resource" "schema_only" {
  type                      = "Microsoft.App/jobs@2025-01-01"
  name                      = "synthetic-schema-only"
  parent_id                 = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/schema-only"
  location                  = "eastus"
  schema_validation_enabled = true

  body = {
    properties = {
      environmentId = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/schema-only/providers/Microsoft.App/managedEnvironments/schema-only"
      configuration = {
        triggerType       = "Manual"
        replicaTimeout    = 60
        replicaRetryLimit = 0
        manualTriggerConfig = {
          parallelism            = 1
          replicaCompletionCount = 1
        }
        identitySettings = [{
          identity  = "/subscriptions/00000000-0000-4000-8000-000000000001/resourceGroups/schema-only/providers/Microsoft.ManagedIdentity/userAssignedIdentities/pull-only"
          lifecycle = "None"
        }]
      }
      template = {
        containers = [{
          name  = "schema-only"
          image = "example.invalid/schema-only@sha256:0000000000000000000000000000000000000000000000000000000000000000"
          resources = {
            cpu    = 0.25
            memory = "0.5Gi"
          }
        }]
      }
    }
  }
}
