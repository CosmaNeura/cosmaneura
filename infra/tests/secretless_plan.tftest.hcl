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
      id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.App/containerApps/mock"
      outbound_ip_addresses = ["198.51.100.10"]
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

  mock_resource "azurerm_monitor_action_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Insights/actionGroups/mock"
    }
  }

  mock_resource "azurerm_monitor_activity_log_alert" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Insights/activityLogAlerts/mock"
    }
  }

  mock_resource "azurerm_monitor_diagnostic_setting" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Insights/diagnosticSettings/mock"
    }
  }

  mock_resource "azurerm_monitor_metric_alert" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Insights/metricAlerts/mock"
    }
  }

  mock_resource "azurerm_monitor_scheduled_query_rules_alert_v2" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Insights/scheduledQueryRules/mock"
    }
  }

  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.DBforPostgreSQL/flexibleServers/mock"
    }
  }

  mock_resource "azurerm_redis_cache" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Cache/Redis/mock"
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

override_data {
  target = data.external.procfile
  values = {
    result = {
      api       = "uv run uvicorn app.main:app"
      scheduler = "uv run celery --app app.celery beat"
      worker    = "uv run celery --app app.celery worker"
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
      STRIPE_ACCOUNT_ID              = ""
      STRIPE_API_VERSION             = "2025-09-30.clover"
      STRIPE_BILLING_ENABLED         = "false"
      STRIPE_INTEGRATION_ENABLED     = "false"
      STRIPE_LIVE_MODE               = "true"
      STRIPE_PORTAL_CONFIGURATION_ID = ""
      STRIPE_PRICE_ID                = ""
      ZOOM_CLIENT_ID                 = "not-a-secret"
      ZOOM_CLIENT_SECRET             = "not-a-secret"
      ZOOM_REDIRECT_URI              = "https://example.invalid/callback"
      ZOOM_SECRET_TOKEN              = "not-a-secret"
      ZOOM_SUCCESS_REDIRECT          = "https://example.invalid/settings"
      ZOOM_WATCHDOG_ENABLED          = "false"
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

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
  }

  assert {
    condition     = length(module.public_phi_observability) == 0
    error_message = "public-PHI observability must remain disabled by default"
  }
  assert {
    condition = alltrue([for name, fallback in local.core_cab_public_defaults :
    one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == fallback])
    error_message = "Core CAB must remain disabled/empty in the API by default"
  }
  assert {
    condition = !contains([for entry in azurerm_container_app.app.secret : entry.name], "billing-source-policy-json") && alltrue([
      for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      alltrue([for entry in workload.template[0].container[0].env : !startswith(entry.name, "CORE_CAB_") && entry.name != "BILLING_SOURCE_POLICY_JSON"])
    ])
    error_message = "No dormant source policy or non-API CAB capability may be exposed"
  }
}

run "public_phi_observability_static_plan" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    enable_public_phi_observability                = true
    production_container_apps_egress_ipv4          = "198.51.100.10"
    public_phi_alert_email                         = "launch-ops@example.invalid"
    public_phi_support_owner                       = "platform-on-call"
  }

  assert {
    condition     = module.public_phi_observability[0].diagnostic_setting_count == 3
    error_message = "the activation plan must contain three metric-only data-service diagnostic settings"
  }

  assert {
    condition     = module.public_phi_observability[0].log_alert_count == 4
    error_message = "the activation plan must contain four fixed-event aggregate log alerts"
  }

  assert {
    condition     = module.public_phi_observability[0].metric_alert_count == 5
    error_message = "the activation plan must contain five dependency-health/capacity metric alerts"
  }

  assert {
    condition     = module.public_phi_observability[0].activity_log_alert_count == 1
    error_message = "the activation plan must contain one PostgreSQL administrative-failure alert"
  }

  assert {
    condition     = module.public_phi_observability[0].all_log_queries_are_aggregate_only
    error_message = "every public-PHI log alert must emit only an aggregate count"
  }

  assert {
    condition     = azurerm_storage_account.uploads.allow_nested_items_to_be_public == false
    error_message = "the upload account must prohibit anonymous nested-item publication"
  }
}

run "managed_identity_activation_static_plan" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule    = false
    deid_managed_identity_compatible_worker_image_tag = "1111111111111111111111111111111111111111"
    enable_deid_managed_identity                      = true
    production_container_apps_egress_ipv4             = "198.51.100.10"
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

run "core_cab_api_only_configured_projection" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
  }
  override_data {
    target = data.external.secrets
    values = {
      result = {
        CORE_CAB_AUTHORIZE_ENABLED                 = "true"
        CORE_CAB_AUTHORIZE_TENANT_ID               = "11111111-1111-1111-1111-111111111111"
        CORE_CAB_AUTHORIZE_AUDIENCE                = "22222222-2222-2222-2222-222222222222"
        CORE_CAB_AUTHORIZE_CALLER_ID               = "33333333-3333-3333-3333-333333333333"
        CORE_CAB_BRIDGE_ENABLED                    = "true"
        CORE_CAB_ASYNC_ENABLED                     = "true"
        CORE_CAB_CONTINUATION_ENABLED              = "true"
        CORE_CAB_ORIGIN                            = "https://cab.example.invalid"
        CORE_CAB_AUDIENCE                          = "44444444-4444-4444-4444-444444444444"
        CORE_CAB_MANAGED_IDENTITY_CLIENT_ID        = "55555555-5555-5555-5555-555555555555"
        BILLING_SOURCE_POLICY_JSON                 = "{\"schemaVersion\":\"core.billing-source-policy.v1\",\"enabled\":false,\"clerkIssuer\":\"\",\"admissions\":[]}"
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
        STRIPE_ACCOUNT_ID              = ""
        STRIPE_API_VERSION             = "2025-09-30.clover"
        STRIPE_BILLING_ENABLED         = "false"
        STRIPE_INTEGRATION_ENABLED     = "false"
        STRIPE_LIVE_MODE               = "true"
        STRIPE_PORTAL_CONFIGURATION_ID = ""
        STRIPE_PRICE_ID                = ""
        ZOOM_CLIENT_ID                 = "not-a-secret"
        ZOOM_CLIENT_SECRET             = "not-a-secret"
        ZOOM_REDIRECT_URI              = "https://example.invalid/callback"
        ZOOM_SECRET_TOKEN              = "not-a-secret"
        ZOOM_SUCCESS_REDIRECT          = "https://example.invalid/settings"
        ZOOM_WATCHDOG_ENABLED          = "false"
      }
    }
  }
  assert {
    condition     = one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.secret_name if entry.name == "BILLING_SOURCE_POLICY_JSON"]) == "billing-source-policy-json" && contains([for secret in azurerm_container_app.app.secret : secret.name], "billing-source-policy-json")
    error_message = "The policy must be an API-only secret reference"
  }
  assert {
    condition = alltrue([for name in ["CORE_CAB_ASYNC_ENABLED", "CORE_CAB_CONTINUATION_ENABLED"] :
    one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == "true"])
    error_message = "Explicit async and continuation flags must reach only the API"
  }
  assert {
    condition = alltrue([for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
    !contains([for secret in workload.secret : secret.name], "billing-source-policy-json") && alltrue([for entry in workload.template[0].container[0].env : !startswith(entry.name, "CORE_CAB_") && entry.name != "BILLING_SOURCE_POLICY_JSON"])])
    error_message = "Configured CAB input must not reach worker or scheduler"
  }
}
