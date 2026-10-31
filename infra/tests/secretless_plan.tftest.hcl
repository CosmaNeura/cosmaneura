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
  mock_data "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-scribe-production/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-scribe-production-database-migrations"
      client_id    = "10000000-0000-4000-8000-000000000003"
      principal_id = "10000000-0000-4000-8000-000000000004"
    }
  }

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
      SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED     = "false"
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
      SCRIBE_CAPTURE_ENABLED             = "forbidden-generic-override"
      SENTRY_DSN                         = ""
      SESSION_SECRET_KEY                 = "not-a-secret"
      SMTP_URL                           = "smtp://placeholder.invalid:1025"
      # Canonical 32-byte Fernet test key (43 URL-safe base64 characters plus
      # one pad). This is fixed non-production test material.
      SOVEREIGN_RECORD_GRANT_SECRET = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
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

  assert {
    condition = alltrue([
      for name, value in local.scribe_native_api_public_values :
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == value
    ]) && alltrue([for value in values(local.scribe_native_api_public_values) : value == "false" || value == ""])
    error_message = "native Scribe and durable Zoom production settings must be explicit and dark by default"
  }

  assert {
    condition = alltrue([
      for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      one([for entry in workload.template[0].container[0].env : entry.value if entry.name == "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED"]) == "false"
    ])
    error_message = "the durable Zoom cutover must remain consistently disabled on every production workload by default"
  }

  assert {
    condition = alltrue([
      for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      alltrue([
        for name in setsubtract(toset(keys(local.scribe_native_api_public_values)), toset(keys(local.zoom_durable_public_values))) :
        !contains([for entry in workload.template[0].container[0].env : entry.name], name)
      ])
    ])
    error_message = "native device and encounter gates must remain API-only"
  }

  assert {
    condition = alltrue([
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == "SCRIBE_WORKFLOW_ENABLED"]) == "false",
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == "SCRIBE_HANDOFF_ENABLED"]) == "false",
    ])
    error_message = "the production wiring must not activate the unprovisioned external workflow or legacy handoff"
  }

  assert {
    condition = (
      azurerm_postgresql_flexible_server.main.authentication[0].active_directory_auth_enabled &&
      azurerm_postgresql_flexible_server.main.authentication[0].password_auth_enabled &&
      azurerm_postgresql_flexible_server.main.authentication[0].tenant_id == "d51754d0-bba5-4810-820e-afac542844d6" &&
      length(azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations) == 0
    )
    error_message = "Production PostgreSQL must retain exact-tenant Entra and password auth by default without granting a migration administrator"
  }
}

run "session_prep_activation_projects_complete_worker_runtime" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    session_prep_production_rollout = {
      enabled             = true
      sovereign_client_id = "11111111-1111-1111-1111-111111111111"
      agent_api_audience  = "22222222-2222-2222-2222-222222222222"
    }
  }

  assert {
    condition = alltrue([
      for workload in [azurerm_container_app.app, azurerm_container_app.worker] :
      one([for entry in workload.template[0].container[0].env : entry.value if entry.name == "AUTHORITY_KERNEL_ENABLED"]) == "true"
    ])
    error_message = "Session Prep activation must enable the Authority Kernel in both API and worker"
  }

  assert {
    condition = alltrue([
      for name, value in local.session_prep_authority_runtime_public_values :
      one([for entry in azurerm_container_app.worker.template[0].container[0].env : entry.value if entry.name == name]) == value
      ]) && one([
      for entry in azurerm_container_app.worker.template[0].container[0].env : entry.value
      if entry.name == "AUTHORITY_CLERK_ISSUER"
    ]) == local.session_prep_clerk_issuer
    error_message = "The Session Prep worker must receive the complete non-secret Authority Kernel runtime contract"
  }

  assert {
    condition = alltrue([
      for name in keys(local.session_prep_api_public_values) :
      !contains([for entry in azurerm_container_app.worker.template[0].container[0].env : entry.name], name)
      ]) && !contains(
      [for entry in azurerm_container_app.worker.template[0].container[0].env : entry.name],
      "SOVEREIGN_RECORD_GRANT_SECRET",
    )
    error_message = "Agent API identity and record-grant capability must remain API-only"
  }
}

run "scribe_core_native_independent_projection" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_production_rollout = {
      capture_enabled                       = false
      capture_long_visits_enabled           = false
      capture_notes_enabled                 = false
      capture_recovery_enabled              = false
      capture_source_enabled                = false
      capture_upload_origins                = []
      capture_working_transcripts_enabled   = false
      combined_session_preparation_enabled  = false
      encounter_creation_enabled            = true
      encounter_prep_enabled                = false
      zoom_durable_capture_bindings_enabled = true
    }
  }

  assert {
    condition = alltrue([
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == "SCRIBE_ENCOUNTER_CREATION_ENABLED"]) == "true",
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == "SCRIBE_CAPTURE_ENABLED"]) == "false",
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED"]) == "true",
      one([for entry in azurerm_container_app.worker.template[0].container[0].env : entry.value if entry.name == "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED"]) == "true",
      one([for entry in azurerm_container_app.scheduler.template[0].container[0].env : entry.value if entry.name == "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED"]) == "true",
    ])
    error_message = "Core-native encounter and durable Zoom gates must project without activating the absent Scribe workflow"
  }
}

run "scribe_capture_complete_projection" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    enable_scribe_workflow                         = true
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_service_base_url                        = "https://scribe.example.invalid"
    scribe_service_scope                           = "api://00000000-0000-0000-0000-000000000000/.default"
    scribe_native_production_rollout = {
      capture_enabled                       = true
      capture_long_visits_enabled           = true
      capture_notes_enabled                 = true
      capture_recovery_enabled              = true
      capture_source_enabled                = true
      capture_upload_origins                = ["https://uploads.example.invalid"]
      capture_working_transcripts_enabled   = true
      combined_session_preparation_enabled  = true
      encounter_creation_enabled            = true
      encounter_prep_enabled                = true
      zoom_durable_capture_bindings_enabled = true
    }
  }

  assert {
    condition = alltrue([
      for name, value in local.scribe_native_api_public_values :
      one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == value
    ]) && local.scribe_native_api_public_values.SCRIBE_CAPTURE_UPLOAD_ORIGINS == "https://uploads.example.invalid"
    error_message = "a complete reviewed Scribe workflow must project every exact native gate and upload origin"
  }
}

run "reject_capture_without_workflow" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_production_rollout = {
      capture_enabled                       = true
      capture_long_visits_enabled           = false
      capture_notes_enabled                 = false
      capture_recovery_enabled              = false
      capture_source_enabled                = false
      capture_upload_origins                = ["https://uploads.example.invalid"]
      capture_working_transcripts_enabled   = false
      combined_session_preparation_enabled  = false
      encounter_creation_enabled            = false
      encounter_prep_enabled                = false
      zoom_durable_capture_bindings_enabled = false
    }
  }

  expect_failures = [var.scribe_native_production_rollout]
}

run "reject_non_origin_upload_target" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_production_rollout = {
      capture_enabled                       = false
      capture_long_visits_enabled           = false
      capture_notes_enabled                 = false
      capture_recovery_enabled              = false
      capture_source_enabled                = false
      capture_upload_origins                = ["https://uploads.example.invalid/container?token=forbidden"]
      capture_working_transcripts_enabled   = false
      combined_session_preparation_enabled  = false
      encounter_creation_enabled            = false
      encounter_prep_enabled                = false
      zoom_durable_capture_bindings_enabled = false
    }
  }

  expect_failures = [var.scribe_native_production_rollout]
}

run "reject_subfeature_without_capture" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_production_rollout = {
      capture_enabled                       = false
      capture_long_visits_enabled           = false
      capture_notes_enabled                 = true
      capture_recovery_enabled              = false
      capture_source_enabled                = false
      capture_upload_origins                = []
      capture_working_transcripts_enabled   = false
      combined_session_preparation_enabled  = false
      encounter_creation_enabled            = false
      encounter_prep_enabled                = false
      zoom_durable_capture_bindings_enabled = false
    }
  }

  expect_failures = [var.scribe_native_production_rollout]
}

run "reject_controlled_rollout_name_in_api_only_secrets" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    python_api_only_secret_vars = [
      "RECALL_WEBHOOK_SECRET",
      "SCRIBE_CAPTURE_ENABLED",
      "SOVEREIGN_RECORD_GRANT_SECRET",
    ]
  }

  expect_failures = [azurerm_container_app.app]
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
        SOVEREIGN_RECORD_GRANT_SECRET      = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
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

run "scribe_database_entra_auth_is_additive_and_exact" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    enable_scribe_database_entra_auth              = true
    enable_scribe_database_migration_administrator = true
    scribe_database_migration_identity_binding = {
      resource_id  = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-scribe-production/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-scribe-production-database-migrations"
      client_id    = "10000000-0000-4000-8000-000000000003"
      principal_id = "10000000-0000-4000-8000-000000000004"
    }
  }

  assert {
    condition = (
      azurerm_postgresql_flexible_server.main.authentication[0].active_directory_auth_enabled &&
      azurerm_postgresql_flexible_server.main.authentication[0].password_auth_enabled &&
      azurerm_postgresql_flexible_server.main.authentication[0].tenant_id == "d51754d0-bba5-4810-820e-afac542844d6"
    )
    error_message = "Scribe database support must add exact-tenant Entra auth without disabling the existing password path"
  }

  assert {
    condition = (
      length(azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations) == 1 &&
      azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations[0].server_name == azurerm_postgresql_flexible_server.main.name &&
      azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations[0].resource_group_name == azurerm_resource_group.main.name &&
      azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations[0].tenant_id == "d51754d0-bba5-4810-820e-afac542844d6" &&
      azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations[0].object_id == var.scribe_database_migration_identity_binding.principal_id &&
      azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations[0].principal_name == "cosmaneura-scribe-production-database-migrations" &&
      azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations[0].principal_type == "ServicePrincipal"
    )
    error_message = "only the exact reviewed Scribe migration UAMI may receive temporary PostgreSQL administrator authority"
  }
}

run "scribe_database_entra_auth_can_remain_after_admin_cleanup" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
  }

  assert {
    condition = (
      azurerm_postgresql_flexible_server.main.authentication[0].active_directory_auth_enabled &&
      azurerm_postgresql_flexible_server.main.authentication[0].password_auth_enabled &&
      length(azurerm_postgresql_flexible_server_active_directory_administrator.scribe_database_migrations) == 0
    )
    error_message = "migration-admin cleanup must preserve both Entra runtime authentication and the existing password rollback path"
  }
}

run "scribe_database_admin_refuses_disabled_entra_auth" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    enable_scribe_database_entra_auth              = false
    enable_scribe_database_migration_administrator = true
    scribe_database_migration_identity_binding = {
      resource_id  = "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/resourceGroups/cosmaneura-scribe-production/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cosmaneura-scribe-production-database-migrations"
      client_id    = "10000000-0000-4000-8000-000000000003"
      principal_id = "10000000-0000-4000-8000-000000000004"
    }
  }

  expect_failures = [
    var.enable_scribe_database_entra_auth,
  ]
}

run "scribe_database_auth_false_override_is_rejected_without_admin" {
  command = plan

  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    enable_scribe_database_entra_auth              = false
  }

  expect_failures = [
    var.enable_scribe_database_entra_auth,
  ]
}

run "scribe_native_workload_without_session_prep" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled   = true
      audience  = "10000000-0000-4000-8000-000000000001"
      client_id = "10000000-0000-4000-8000-000000000002"
    }
  }
  assert {
    condition = alltrue([for name, value in {
      SCRIBE_NATIVE_WORKLOAD_ENABLED                = "true"
      SCRIBE_NATIVE_WORKLOAD_AUDIENCE               = "10000000-0000-4000-8000-000000000001"
      SCRIBE_NATIVE_WORKLOAD_CLIENT_ID              = "10000000-0000-4000-8000-000000000002"
      SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED        = "false"
      SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED = "false"
      SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED        = "false"
      AGENT_API_ENABLED                             = "false"
      AUTHORITY_KERNEL_ENABLED                      = "false"
      SESSION_PREP_PROVIDER_ENABLED                 = "false"
      SCRIBE_CAPTURE_ENABLED                        = "false"
      SCRIBE_ENCOUNTER_CREATION_ENABLED             = "false"
    } : one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == value])
    error_message = "native singleton configuration must not enable Session Prep, global Agent API/Authority Kernel or capture/encounters"
  }
  assert {
    condition = alltrue([for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      length([for entry in workload.template[0].container[0].env : entry.name if startswith(entry.name, "SCRIBE_NATIVE_WORKLOAD_") || entry.name == "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED"]) == 0
    ])
    error_message = "native workload identity and audience are API-only"
  }
}

run "scribe_native_provider_context_is_read_only_api_gate" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled                  = true
      audience                 = "10000000-0000-4000-8000-000000000001"
      client_id                = "10000000-0000-4000-8000-000000000002"
      provider_context_enabled = true
    }
  }
  assert {
    condition = alltrue([for name, value in {
      SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED        = "true"
      SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED = "false"
      SCRIBE_ENCOUNTER_CREATION_ENABLED             = "false"
      SCRIBE_CAPTURE_ENABLED                        = "false"
      SESSION_PREP_PROVIDER_ENABLED                 = "false"
      AUTHORITY_KERNEL_ENABLED                      = "false"
    } : one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == value])
    error_message = "private context reads must not activate capture, creation, global authority or Session Prep"
  }
  assert {
    condition = alltrue([for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      length([for entry in workload.template[0].container[0].env : entry.name if entry.name == "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED"]) == 0
    ])
    error_message = "private context reads are API-only"
  }
}

run "reject_native_provider_context_without_workload" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled                  = false
      audience                 = ""
      client_id                = ""
      provider_context_enabled = true
    }
  }
  expect_failures = [var.scribe_native_workload_rollout]
}

run "reject_native_workload_zero_caller" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled   = true
      audience  = "10000000-0000-4000-8000-000000000001"
      client_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  expect_failures = [var.scribe_native_workload_rollout]
}

run "reject_native_workload_shadow" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    python_api_only_secret_vars                    = ["SCRIBE_NATIVE_WORKLOAD_CLIENT_ID"]
  }
  expect_failures = [azurerm_container_app.app]
}


run "scribe_native_existing_patient_visits_require_context" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled                         = true
      audience                        = "11111111-1111-4111-8111-111111111111"
      client_id                       = "22222222-2222-4222-8222-222222222222"
      existing_patient_visits_enabled = true
    }
  }
  expect_failures = [var.scribe_native_workload_rollout]
}

run "scribe_native_existing_patient_visits_do_not_enable_global_gates" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled                         = true
      audience                        = "11111111-1111-4111-8111-111111111111"
      client_id                       = "22222222-2222-4222-8222-222222222222"
      provider_context_enabled        = true
      existing_patient_visits_enabled = true
    }
  }
  assert {
    condition     = lookup({ for entry in azurerm_container_app.app.template[0].container[0].env : entry.name => entry.value }, "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED", "") == "true"
    error_message = "Existing-patient gate must be projected to the existing API."
  }
  assert {
    condition     = alltrue([for name in ["AUTHORITY_KERNEL_ENABLED", "AGENT_API_ENABLED", "SESSION_PREP_PROVIDER_ENABLED", "SCRIBE_ENCOUNTER_CREATION_ENABLED", "SCRIBE_CAPTURE_ENABLED"] : lookup({ for entry in azurerm_container_app.app.template[0].container[0].env : entry.name => entry.value }, name, "false") == "false"])
    error_message = "Native visit selection must not enable other global or capture gates."
  }
  assert {
    condition = alltrue([for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      length([for entry in workload.template[0].container[0].env : entry.name if entry.name == "SCRIBE_NATIVE_EXISTING_PATIENT_VISITS_ENABLED"]) == 0
    ])
    error_message = "The native visit gate is API-only."
  }
}

run "scribe_native_v2_full_actions_use_existing_api_only" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled                         = true
      audience                        = "11111111-1111-4111-8111-111111111111"
      client_id                       = "22222222-2222-4222-8222-222222222222"
      provider_context_enabled        = true
      existing_patient_visits_enabled = true
      native_visit_actions_v2_enabled = true
    }
  }
  assert {
    condition     = one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED"]) == "true"
    error_message = "The typed full-v2 gate must reach only the existing Core API."
  }
  assert {
    condition = alltrue([for workload in [azurerm_container_app.worker, azurerm_container_app.scheduler] :
      length([for entry in workload.template[0].container[0].env : entry.name if entry.name == "SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED"]) == 0
    ])
    error_message = "Full-v2 selection must not enable a worker or scheduler gate."
  }
  assert {
    condition     = alltrue([for name in ["AUTHORITY_KERNEL_ENABLED", "AGENT_API_ENABLED", "SESSION_PREP_PROVIDER_ENABLED", "SCRIBE_ENCOUNTER_CREATION_ENABLED", "SCRIBE_CAPTURE_ENABLED", "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED"] : one([for entry in azurerm_container_app.app.template[0].container[0].env : entry.value if entry.name == name]) == "false"])
    error_message = "Full native visit selection must not change separate global, capture or Zoom controls."
  }
}

run "scribe_native_v2_full_actions_require_existing_visits" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_native_workload_rollout = {
      enabled                         = true
      audience                        = "11111111-1111-4111-8111-111111111111"
      client_id                       = "22222222-2222-4222-8222-222222222222"
      provider_context_enabled        = true
      existing_patient_visits_enabled = false
      native_visit_actions_v2_enabled = true
    }
  }
  expect_failures = [var.scribe_native_workload_rollout]
}

run "scribe_native_v2_full_actions_reject_public_shadow" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    python_public_vars                             = ["SCRIBE_NATIVE_VISIT_ACTIONS_V2_ENABLED"]
  }
  expect_failures = [var.python_public_vars]
}

run "scribe_bundle_default_keeps_three_containers_dark" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
  }
  assert {
    condition     = local.scribe_bundle_api_values == { SCRIBE_BUNDLE_API_ENABLED = "false" } && local.scribe_bundle_worker_values == { SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED = "false" } && local.scribe_bundle_scheduler_values == local.scribe_bundle_worker_values && length(data.azurerm_user_assigned_identity.scribe_bundle_translation) == 0 && length(azurerm_container_app.worker.identity[0].identity_ids) == 1
    error_message = "default bundle must not attach an identity, stage configuration or activate any of the three Core containers"
  }
}

run "scribe_bundle_does_not_shadow_generic_settings" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    python_api_only_secret_vars                    = ["SCRIBE_BUNDLE_API_ENABLED"]
  }
  expect_failures = [azurerm_container_app.app]
}

run "scribe_bundle_worker_requires_separate_identity_preparation" {
  command = plan
  variables {
    adopt_existing_production_postgres_egress_rule = false
    production_container_apps_egress_ipv4          = "198.51.100.10"
    scribe_core_bundle                             = { captured_worker_enabled = true }
  }
  expect_failures = [var.scribe_core_bundle]
}
