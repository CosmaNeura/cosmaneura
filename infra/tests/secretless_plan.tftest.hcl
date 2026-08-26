mock_provider "azapi" {
  mock_resource "azapi_resource" {
    defaults = {
      output = {
        id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.App/managedEnvironments/mock/certificates/mock"
      }
    }
  }
}

mock_provider "azurerm" {
  mock_resource "azurerm_ai_services" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.CognitiveServices/accounts/mock"
    }
  }

  mock_resource "azurerm_container_app" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.App/containerApps/mock"
    }
  }

  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.App/managedEnvironments/mock"
    }
  }

  mock_resource "azurerm_container_registry" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.ContainerRegistry/registries/mock"
    }
  }

  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.OperationalInsights/workspaces/mock"
    }
  }

  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.DBforPostgreSQL/flexibleServers/mock"
    }
  }

  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mock"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock"
    }
  }
}

mock_provider "external" {}
mock_provider "onepassword" {}
mock_provider "random" {}

override_data {
  target = data.external.procfile
  values = {
    result = {
      api         = "uv run uvicorn app.main:app"
      job_monitor = "uv run celery --app app.celery flower --enable_events=False --persistent=False --tasks_columns=name,uuid,state,received,started,runtime,worker"
      scheduler   = "uv run celery --app app.celery beat"
      worker      = "uv run celery --app app.celery worker"
    }
  }
}

override_data {
  target = data.external.secrets
  values = {
    result = {
      ALLOWED_HOST_LIST                          = "example.invalid"
      API_BASE_URL                               = "https://api.example.invalid"
      ASSEMBLY_API_KEY                           = "not-a-secret"
      AZURE_DEID_CLIENT_ID                       = "00000000-0000-0000-0000-000000000000"
      AZURE_DEID_CLIENT_SECRET                   = "not-a-secret"
      AZURE_DEID_ENDPOINT                        = "https://example.invalid"
      AZURE_DEID_TENANT_ID                       = "00000000-0000-0000-0000-000000000000"
      AZURE_OPENAI_API_KEY                       = "not-a-secret"
      AZURE_OPENAI_ENDPOINT                      = "https://example.invalid"
      AZURE_UPLOADS_ACCOUNT_KEY                  = "not-a-secret"
      AZURE_UPLOADS_ACCOUNT_NAME                 = "placeholder"
      AZURE_UPLOADS_ACCOUNT_URL                  = "https://example.invalid"
      AZURE_UPLOADS_CONTAINER_NAME               = "uploads"
      AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME = "example-notes"
      AUTHORITY_CLERK_ISSUER                     = "https://clerk.example.invalid"
      CLERK_PRIVATE_KEY                          = "not-a-secret"
      DATABASE_URL                               = "postgresql://placeholder.invalid/app"
      DATABASE_URL_SECRET_NAME                   = "database-url"
      DATABASE_URL_V2                            = ""
      DATABASE_URL_V2_ENABLED                    = "false"
      DURABLE_WEBHOOK_INBOX_ENABLED              = "false"
      EMAIL_FROM_ADDRESS                         = "noreply@example.invalid"
      ENCRYPTION_KEY                             = "not-a-secret"
      ENTRA_TENANT_ID                            = "00000000-0000-0000-0000-000000000000"
      INTAKE_CHAT_DOMAIN                         = "example.invalid"
      LOG_LEVEL                                  = "info"
      PORT                                       = "80"
      POSTGRES_PASSWORD                          = "not-a-secret"
      POSTGRES_USERNAME                          = "placeholder"
      RECALL_API_BASE                            = "https://example.invalid"
      RECALL_API_KEY                             = "not-a-secret"
      # Deliberately fake, non-production key with the required Recall/Svix
      # whsec_ + base64 syntax so the static plan exercises the shape guard.
      RECALL_WEBHOOK_SECRET              = "whsec_c3RhdGljLXRlc3Qta2V5IQ=="
      REDIS_URL                          = "redis://placeholder.invalid/1"
      SCRIBE_API_AUDIENCE                = "00000000-0000-0000-0000-000000000000"
      SCRIBE_CALLER_CLIENT_ID            = ""
      SCRIBE_HANDOFF_ENABLED             = "false"
      SCRIBE_RECONCILER_CALLER_CLIENT_ID = ""
      SENTRY_DSN                         = ""
      SESSION_SECRET_KEY                 = "not-a-secret"
      SMTP_URL                           = "smtp://placeholder.invalid:1025"
      SOVEREIGN_RECORD_GRANT_SECRET      = "not-a-secret"
      # Production must always declare live/test mode explicitly, even while
      # both default-off Stripe gates are disabled. Keep the PR plan inert
      # while exercising that fail-closed configuration invariant.
      STRIPE_ACCOUNT_ID          = ""
      STRIPE_API_VERSION         = "2025-09-30.clover"
      STRIPE_BILLING_ENABLED     = "false"
      STRIPE_INTEGRATION_ENABLED = "false"
      STRIPE_LIVE_MODE           = "true"
      STRIPE_PRICE_ID            = ""
      ZOOM_CLIENT_ID             = "not-a-secret"
      ZOOM_CLIENT_SECRET         = "not-a-secret"
      ZOOM_REDIRECT_URI          = "https://example.invalid/callback"
      ZOOM_SECRET_TOKEN          = "not-a-secret"
      ZOOM_SUCCESS_REDIRECT      = "https://example.invalid/settings"
    }
  }
}

override_data {
  target = data.external.database_url_live_guard
  values = {
    result = {
      result = "verified"
    }
  }
}

override_data {
  target = data.external.deid_managed_identity_live_guard
  values = {
    result = {
      check_count = "5"
      result      = "verified"
    }
  }
}

# Override the built-in remote-state data source as well as every provider so
# pull-request code cannot reach production state or cloud APIs.
override_data {
  target = data.terraform_remote_state.tfstate
  values = {
    outputs = {}
  }
}

run "secretless_static_plan" {
  command = plan
}

run "managed_identity_activation_static_plan" {
  command = plan

  variables {
    deid_managed_identity_compatible_worker_image_tag = "1111111111111111111111111111111111111111"
    enable_deid_managed_identity                      = true
  }

  assert {
    condition     = azurerm_role_assignment.health_services_deid_access.role_definition_name == "DeID Realtime Data User"
    error_message = "the DeID identity must retain only the realtime-user role"
  }

  assert {
    condition     = endswith(azurerm_role_assignment.health_services_deid_access.scope, "/providers/Microsoft.HealthDataAIServices/deidServices/AnonymizerAPI")
    error_message = "the DeID role must be scoped to the exact AnonymizerAPI resource"
  }

  assert {
    condition     = contains(azurerm_container_app.worker.identity[0].identity_ids, azurerm_user_assigned_identity.health_services.id)
    error_message = "the production worker must attach the exact DeID user-assigned identity"
  }

  assert {
    condition = length([
      for setting in azurerm_container_app.worker.template[0].container[0].env : setting
      if setting.name == "AZURE_DEID_AUTH_MODE" && setting.value == "managed_identity"
    ]) == 1
    error_message = "managed-identity activation must select the explicit worker auth mode"
  }
}
