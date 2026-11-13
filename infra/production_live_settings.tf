# T-CORE-PIN: non-secret production Container Apps settings equal to the
# 2026-11-13 14:07 ET live snapshot. Terraform Production Plan takes no
# -var inputs; with use_production_live_settings_pin=true (the default) these
# locals are the effective values so a print-only plan does not remove or
# flip live env. Provider-mocked tests set the pin false at file scope.

variable "use_production_live_settings_pin" {
  description = "When true (production default), project the reviewed live settings pin. Secretless tests disable this so dark variable defaults remain additive-safe."
  type        = bool
  default     = true
}

locals {
  production_live_scribe_native_workload_rollout = {
    enabled                         = true
    audience                        = "af957741-0342-498b-8496-e93706af9e99"
    client_id                       = "c7bae168-fbf5-4658-b0d9-dd4b5373a1be"
    provider_context_enabled        = true
    existing_patient_visits_enabled = true
    native_visit_actions_v2_enabled = true
  }

  production_live_scribe_native_production_rollout = {
    capture_enabled                       = true
    capture_long_visits_enabled           = false
    capture_notes_enabled                 = true
    capture_recovery_enabled              = false
    capture_source_enabled                = false
    capture_upload_origins                = toset(["https://cosmaneurascribeprod.blob.core.windows.net"])
    capture_working_transcripts_enabled   = false
    combined_session_preparation_enabled  = false
    encounter_creation_enabled            = false
    encounter_prep_enabled                = false
    zoom_durable_capture_bindings_enabled = false
  }

  production_live_scribe_cancellation_production_rollout = {
    ledger_ready  = true
    enabled       = true
    grace_days    = 90
    phi_scope     = "core-and-scribe-clinical-records"
    policy_sha256 = "0855d28a58941adcf5d8899bb1bcfc670070e24e2b25674fbe5235a77a5f00d8"
  }

  production_live_scribe_workflow_client_production_rollout = {
    identity_reuse_enabled = true
    http_pool_enabled      = true
  }

  production_live_enable_scribe_workflow         = true
  production_live_scribe_service_base_url        = "https://app.cosmaneura.com"
  production_live_scribe_service_scope           = "api://57264fe1-6a4a-4946-8412-e0be18805977/.default"
  production_live_scribe_service_timeout_seconds = 60

  # Exact live non-secret bundle JSON (schema_version 1). Kept as strings so
  # plan/apply project byte-identical values to the 14:07 ET snapshot.
  production_live_scribe_core_bundle = {
    api_enabled                          = true
    captured_worker_enabled              = true
    attach_existing_translation_identity = true
    api_configuration_json               = "{\"enabled\":true,\"environment\":{\"COSMANEURA_SCRIBE_AUDIO_ENCRYPTION_KEY_ID\":\"https://cosmaneura-scribe-prod.vault.azure.net/keys/session-audio-wrapping\",\"COSMANEURA_SCRIBE_CAPTURE_PROTOCOL_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_CONTINUATION_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_TENANT_IDS\":\"[\\\"c2aaa4ae-404c-4e95-8330-4d2e12976aec\\\"]\",\"COSMANEURA_SCRIBE_CONFIGURED_WORKER_DATABASE_PERMISSIONS_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_DATABASE_HOST\":\"cosmaneura-prod-postgres.postgres.database.azure.com\",\"COSMANEURA_SCRIBE_DATABASE_MAX_OVERFLOW\":\"0\",\"COSMANEURA_SCRIBE_DATABASE_NAME\":\"cosmaneura_scribe\",\"COSMANEURA_SCRIBE_DATABASE_POOL_SIZE\":\"2\",\"COSMANEURA_SCRIBE_DATABASE_PORT\":\"5432\",\"COSMANEURA_SCRIBE_DATABASE_RUNTIME_PRINCIPAL_NAME\":\"cosmaneura-prod-scribe-workflow\",\"COSMANEURA_SCRIBE_DATABASE_RUNTIME_PRINCIPAL_OBJECT_ID\":\"8fecefc9-1577-4afa-b353-83beb5f793f7\",\"COSMANEURA_SCRIBE_DATABASE_SSL\":\"true\",\"COSMANEURA_SCRIBE_DATABASE_TRANSCRIPTION_PRINCIPAL_NAME\":\"cosmaneura-prod-health-services\",\"COSMANEURA_SCRIBE_DATABASE_TRANSCRIPTION_PRINCIPAL_OBJECT_ID\":\"36cc13e3-293b-45a6-9819-4cee01458de6\",\"COSMANEURA_SCRIBE_DATABASE_TRANSLATION_PRINCIPAL_NAME\":\"cosmaneura-scribe-production-runtime\",\"COSMANEURA_SCRIBE_DATABASE_TRANSLATION_PRINCIPAL_OBJECT_ID\":\"3f712a80-f393-49d0-ab42-6a40bc50dfbd\",\"COSMANEURA_SCRIBE_DATABASE_USER\":\"cosmaneura-prod-scribe-workflow\",\"COSMANEURA_SCRIBE_DELIVERY_RECONCILER_JOB_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_DERIVED_WRITER_DATABASE_PERMISSIONS_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_ENTRA_TENANT_ID\":\"d51754d0-bba5-4810-820e-afac542844d6\",\"COSMANEURA_SCRIBE_MANAGED_IDENTITY_CLIENT_ID\":\"c7bae168-fbf5-4658-b0d9-dd4b5373a1be\",\"COSMANEURA_SCRIBE_MASTER_PLATFORM_HANDOFF_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_REPOSITORY_BACKEND\":\"postgres\",\"COSMANEURA_SCRIBE_STORAGE_ACCOUNT_URL\":\"https://cosmaneurascribeprod.blob.core.windows.net/\",\"COSMANEURA_SCRIBE_STORAGE_BACKEND\":\"azure\",\"COSMANEURA_SCRIBE_STORAGE_CONTAINER_NAME\":\"audio\",\"COSMANEURA_SCRIBE_TRANSCRIPTION_BACKEND\":\"none\",\"COSMANEURA_SCRIBE_TRANSCRIPTION_JOB_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_TRUSTED_PLATFORM_CLIENT_ID\":\"c7bae168-fbf5-4658-b0d9-dd4b5373a1be\",\"COSMANEURA_SCRIBE_UPLOAD_GRANT_TTL_SECONDS\":\"900\",\"COSMANEURA_SCRIBE_WORKFLOW_API_AUDIENCE\":\"57264fe1-6a4a-4946-8412-e0be18805977\",\"COSMANEURA_SCRIBE_WORKFLOW_AUTHENTICATION_ENABLED\":\"true\"},\"schema_version\":1}"
    receiver_configuration_json          = "{\"authority_backend\":\"core-provider-context-v1\",\"beta_box_id\":\"c2aaa4ae-404c-4e95-8330-4d2e12976aec\",\"core_scope\":\"api://af957741-0342-498b-8496-e93706af9e99/.default\",\"database_host\":null,\"database_name\":null,\"database_reader_role\":null,\"desktop_native_session_without_origin\":false,\"enabled\":true,\"encounter_creation_enabled\":true,\"environment_id\":\"86205a3e-0c29-5030-959f-e3352f6e9c9b\",\"ingress_isolation_receipt\":\"sha256:a7aa9bfeb53fc9378d3cf4acfde74fd7a30c8af685c81c56a8a90f579d78a777\",\"managed_identity_client_id\":\"c7bae168-fbf5-4658-b0d9-dd4b5373a1be\",\"native_visit_actions_v2_enabled\":true,\"platforms\":[\"ios\",\"desktop\"],\"production\":{\"audience\":null,\"core_origin\":\"https://app.cosmaneura.com\",\"deployment_receipt_sha256\":\"8c90a3890e0548592eb6b7ce42200dafaa4a45e661ac1f4c172545f026d43c33\",\"issuer\":\"https://clerk.cosmaneura.com\",\"native_policy_receipt_sha256\":\"02f38a90d125a84dbe48b1f0f2aaabab1c3d76d97543e7619efe7f825af63136\",\"organization_id\":null,\"protected_data_receipt_sha256\":\"d49057bed0493698c382f613872d6976d853aff95d0178e3bf9fa9f6506d1702\",\"receiver_origin\":\"https://app.cosmaneura.com\",\"session_claims_receipt_sha256\":\"0849efa1aced5ca3a540fea07440f8f08f531efabdd16dfed9e0ae4b98aefac6\"},\"profile\":\"core-production-personal-v1\",\"schema\":1}"
    receiver_contract_sha256             = "97008da096194099a55fa8c1f0d8e32fb0cbf3791802a8fa719f3b3f6940e19a"
    speech_configuration_json            = "{\"enabled\":true,\"environment\":{\"COSMANEURA_SCRIBE_AUDIO_ASSEMBLY_ENCRYPTED_TEMP_DIRECTORY\":\"/opt/scribe/encrypted-audio\",\"COSMANEURA_SCRIBE_AUDIO_ENCRYPTION_KEY_ID\":\"https://cosmaneura-scribe-prod.vault.azure.net/keys/session-audio-wrapping\",\"COSMANEURA_SCRIBE_AZURE_SPEECH_ENDPOINT\":\"https://cosmaneuraprodopenai.cognitiveservices.azure.com\",\"COSMANEURA_SCRIBE_CAPTURED_WORKER_BATCH_SIZE\":\"1\",\"COSMANEURA_SCRIBE_CAPTURED_WORKER_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_CAPTURED_WORKER_STAGE\":\"speech\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_CONTINUATION_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_TENANT_IDS\":\"[\\\"c2aaa4ae-404c-4e95-8330-4d2e12976aec\\\"]\",\"COSMANEURA_SCRIBE_CONFIGURED_WORKER_DATABASE_PERMISSIONS_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_DATABASE_HOST\":\"cosmaneura-prod-postgres.postgres.database.azure.com\",\"COSMANEURA_SCRIBE_DATABASE_MAX_OVERFLOW\":\"0\",\"COSMANEURA_SCRIBE_DATABASE_NAME\":\"cosmaneura_scribe\",\"COSMANEURA_SCRIBE_DATABASE_POOL_SIZE\":\"1\",\"COSMANEURA_SCRIBE_DATABASE_PORT\":\"5432\",\"COSMANEURA_SCRIBE_DATABASE_RUNTIME_PRINCIPAL_NAME\":\"cosmaneura-prod-scribe-workflow\",\"COSMANEURA_SCRIBE_DATABASE_RUNTIME_PRINCIPAL_OBJECT_ID\":\"8fecefc9-1577-4afa-b353-83beb5f793f7\",\"COSMANEURA_SCRIBE_DATABASE_SSL\":\"true\",\"COSMANEURA_SCRIBE_DATABASE_TRANSCRIPTION_PRINCIPAL_NAME\":\"cosmaneura-prod-health-services\",\"COSMANEURA_SCRIBE_DATABASE_TRANSCRIPTION_PRINCIPAL_OBJECT_ID\":\"36cc13e3-293b-45a6-9819-4cee01458de6\",\"COSMANEURA_SCRIBE_DATABASE_TRANSLATION_PRINCIPAL_NAME\":\"cosmaneura-scribe-production-runtime\",\"COSMANEURA_SCRIBE_DATABASE_TRANSLATION_PRINCIPAL_OBJECT_ID\":\"3f712a80-f393-49d0-ab42-6a40bc50dfbd\",\"COSMANEURA_SCRIBE_DATABASE_USER\":\"cosmaneura-prod-health-services\",\"COSMANEURA_SCRIBE_DELIVERY_RECONCILER_JOB_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_DERIVED_WRITER_DATABASE_PERMISSIONS_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_EXISTING_CORE_PUBLIC_AI_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_MANAGED_IDENTITY_CLIENT_ID\":\"5696041c-ee89-4ef5-bc5d-fdf846cd7364\",\"COSMANEURA_SCRIBE_MASTER_PLATFORM_HANDOFF_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_REPOSITORY_BACKEND\":\"postgres\",\"COSMANEURA_SCRIBE_STORAGE_ACCOUNT_URL\":\"https://cosmaneurascribeprod.blob.core.windows.net/\",\"COSMANEURA_SCRIBE_STORAGE_BACKEND\":\"azure\",\"COSMANEURA_SCRIBE_STORAGE_CONTAINER_NAME\":\"audio\",\"COSMANEURA_SCRIBE_TRANSCRIPTION_BACKEND\":\"azure\",\"COSMANEURA_SCRIBE_TRANSCRIPTION_JOB_ENABLED\":\"false\"},\"schema_version\":1}"
    translation_configuration_json       = "{\"enabled\":true,\"environment\":{\"COSMANEURA_SCRIBE_AUDIO_ENCRYPTION_KEY_ID\":\"https://cosmaneura-scribe-prod.vault.azure.net/keys/session-audio-wrapping\",\"COSMANEURA_SCRIBE_AZURE_TRANSLATION_DEPLOYMENT\":\"gpt-4.1-2025-04-14\",\"COSMANEURA_SCRIBE_AZURE_TRANSLATION_ENDPOINT\":\"https://cosmaneuraprodopenai.openai.azure.com/openai/v1\",\"COSMANEURA_SCRIBE_CAPTURED_WORKER_BATCH_SIZE\":\"1\",\"COSMANEURA_SCRIBE_CAPTURED_WORKER_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_CAPTURED_WORKER_STAGE\":\"translation\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_CONTINUATION_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_CONFIGURED_CAPTURE_TENANT_IDS\":\"[\\\"c2aaa4ae-404c-4e95-8330-4d2e12976aec\\\"]\",\"COSMANEURA_SCRIBE_CONFIGURED_WORKER_DATABASE_PERMISSIONS_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_DATABASE_HOST\":\"cosmaneura-prod-postgres.postgres.database.azure.com\",\"COSMANEURA_SCRIBE_DATABASE_MAX_OVERFLOW\":\"0\",\"COSMANEURA_SCRIBE_DATABASE_NAME\":\"cosmaneura_scribe\",\"COSMANEURA_SCRIBE_DATABASE_POOL_SIZE\":\"1\",\"COSMANEURA_SCRIBE_DATABASE_PORT\":\"5432\",\"COSMANEURA_SCRIBE_DATABASE_RUNTIME_PRINCIPAL_NAME\":\"cosmaneura-prod-scribe-workflow\",\"COSMANEURA_SCRIBE_DATABASE_RUNTIME_PRINCIPAL_OBJECT_ID\":\"8fecefc9-1577-4afa-b353-83beb5f793f7\",\"COSMANEURA_SCRIBE_DATABASE_SSL\":\"true\",\"COSMANEURA_SCRIBE_DATABASE_TRANSCRIPTION_PRINCIPAL_NAME\":\"cosmaneura-prod-health-services\",\"COSMANEURA_SCRIBE_DATABASE_TRANSCRIPTION_PRINCIPAL_OBJECT_ID\":\"36cc13e3-293b-45a6-9819-4cee01458de6\",\"COSMANEURA_SCRIBE_DATABASE_TRANSLATION_PRINCIPAL_NAME\":\"cosmaneura-scribe-production-runtime\",\"COSMANEURA_SCRIBE_DATABASE_TRANSLATION_PRINCIPAL_OBJECT_ID\":\"3f712a80-f393-49d0-ab42-6a40bc50dfbd\",\"COSMANEURA_SCRIBE_DATABASE_USER\":\"cosmaneura-scribe-production-runtime\",\"COSMANEURA_SCRIBE_DELIVERY_RECONCILER_JOB_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_DERIVED_WRITER_DATABASE_PERMISSIONS_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_EXISTING_CORE_PUBLIC_AI_ENABLED\":\"true\",\"COSMANEURA_SCRIBE_MANAGED_IDENTITY_CLIENT_ID\":\"39d00a14-ca79-4cbf-8821-3e2af90ea0e2\",\"COSMANEURA_SCRIBE_MASTER_PLATFORM_HANDOFF_ENABLED\":\"false\",\"COSMANEURA_SCRIBE_REPOSITORY_BACKEND\":\"postgres\",\"COSMANEURA_SCRIBE_STORAGE_BACKEND\":\"memory\",\"COSMANEURA_SCRIBE_TRANSCRIPTION_BACKEND\":\"none\",\"COSMANEURA_SCRIBE_TRANSCRIPTION_JOB_ENABLED\":\"false\"},\"schema_version\":1}"
  }

  effective_scribe_native_workload_rollout = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_native_workload_rollout
    : var.scribe_native_workload_rollout
  )
  effective_scribe_native_production_rollout = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_native_production_rollout
    : var.scribe_native_production_rollout
  )
  effective_scribe_cancellation_production_rollout = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_cancellation_production_rollout
    : var.scribe_cancellation_production_rollout
  )
  effective_scribe_workflow_client_production_rollout = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_workflow_client_production_rollout
    : var.scribe_workflow_client_production_rollout
  )
  effective_enable_scribe_workflow = (
    var.use_production_live_settings_pin
    ? local.production_live_enable_scribe_workflow
    : var.enable_scribe_workflow
  )
  effective_scribe_service_base_url = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_service_base_url
    : var.scribe_service_base_url
  )
  effective_scribe_service_scope = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_service_scope
    : var.scribe_service_scope
  )
  effective_scribe_service_timeout_seconds = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_service_timeout_seconds
    : var.scribe_service_timeout_seconds
  )
  effective_scribe_core_bundle = (
    var.use_production_live_settings_pin
    ? local.production_live_scribe_core_bundle
    : var.scribe_core_bundle
  )

  # When the pin is on, the nine source-controlled pin inputs must remain at
  # their dark defaults so plan/apply cannot mix -var overrides with the live
  # locals. Effective values below are validated independently of the vars.
  production_live_pin_inputs_remain_default = (
    !var.use_production_live_settings_pin || (
      var.enable_scribe_workflow == false &&
      var.scribe_service_base_url == "" &&
      var.scribe_service_scope == "" &&
      var.scribe_service_timeout_seconds == 60 &&
      var.scribe_cancellation_production_rollout.ledger_ready == false &&
      var.scribe_cancellation_production_rollout.enabled == false &&
      var.scribe_cancellation_production_rollout.grace_days == 90 &&
      var.scribe_cancellation_production_rollout.phi_scope == "core-and-scribe-clinical-records" &&
      var.scribe_cancellation_production_rollout.policy_sha256 == "" &&
      var.scribe_workflow_client_production_rollout.identity_reuse_enabled == false &&
      var.scribe_workflow_client_production_rollout.http_pool_enabled == false &&
      var.scribe_native_workload_rollout.enabled == false &&
      var.scribe_native_workload_rollout.audience == "" &&
      var.scribe_native_workload_rollout.client_id == "" &&
      var.scribe_native_workload_rollout.provider_context_enabled == false &&
      var.scribe_native_workload_rollout.existing_patient_visits_enabled == false &&
      var.scribe_native_workload_rollout.native_visit_actions_v2_enabled == false &&
      var.scribe_native_production_rollout.capture_enabled == false &&
      var.scribe_native_production_rollout.capture_long_visits_enabled == false &&
      var.scribe_native_production_rollout.capture_notes_enabled == false &&
      var.scribe_native_production_rollout.capture_recovery_enabled == false &&
      var.scribe_native_production_rollout.capture_source_enabled == false &&
      length(var.scribe_native_production_rollout.capture_upload_origins) == 0 &&
      var.scribe_native_production_rollout.capture_working_transcripts_enabled == false &&
      var.scribe_native_production_rollout.combined_session_preparation_enabled == false &&
      var.scribe_native_production_rollout.encounter_creation_enabled == false &&
      var.scribe_native_production_rollout.encounter_prep_enabled == false &&
      var.scribe_native_production_rollout.zoom_durable_capture_bindings_enabled == false &&
      var.scribe_core_bundle.api_enabled == false &&
      var.scribe_core_bundle.captured_worker_enabled == false &&
      var.scribe_core_bundle.attach_existing_translation_identity == false &&
      var.scribe_core_bundle.api_configuration_json == "" &&
      var.scribe_core_bundle.receiver_configuration_json == "" &&
      var.scribe_core_bundle.receiver_contract_sha256 == "" &&
      var.scribe_core_bundle.speech_configuration_json == "" &&
      var.scribe_core_bundle.translation_configuration_json == ""
    )
  )

  production_live_effective_cancellation_valid = (
    !local.effective_scribe_cancellation_production_rollout.enabled || (
      local.effective_scribe_cancellation_production_rollout.ledger_ready &&
      local.effective_scribe_cancellation_production_rollout.grace_days >= 30 &&
      local.effective_scribe_cancellation_production_rollout.grace_days <= 90 &&
      tostring(local.effective_scribe_cancellation_production_rollout.grace_days) == tostring(floor(local.effective_scribe_cancellation_production_rollout.grace_days)) &&
      local.effective_scribe_cancellation_production_rollout.phi_scope == "core-and-scribe-clinical-records" &&
      can(regex("^[0-9a-f]{64}$", local.effective_scribe_cancellation_production_rollout.policy_sha256))
    )
  )

  # Mirrors infra/variables.tf scribe_native_workload_rollout (241-268) and
  # scribe_native_production_rollout (301-328) against effective_* so the live
  # pin path cannot bypass those contracts.
  production_live_effective_native_workload_valid = (
    (
      !local.effective_scribe_native_workload_rollout.enabled || (
        can(regex("^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", local.effective_scribe_native_workload_rollout.audience)) &&
        local.effective_scribe_native_workload_rollout.audience != "00000000-0000-0000-0000-000000000000" &&
        can(regex("^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", local.effective_scribe_native_workload_rollout.client_id)) &&
        local.effective_scribe_native_workload_rollout.client_id != "00000000-0000-0000-0000-000000000000" &&
        local.effective_scribe_native_workload_rollout.client_id != var.session_prep_production_rollout.sovereign_client_id
      )
      ) && (
      !local.effective_scribe_native_workload_rollout.provider_context_enabled ||
      local.effective_scribe_native_workload_rollout.enabled
      ) && (
      !local.effective_scribe_native_workload_rollout.existing_patient_visits_enabled || (
        local.effective_scribe_native_workload_rollout.enabled &&
        local.effective_scribe_native_workload_rollout.provider_context_enabled
      )
      ) && (
      !local.effective_scribe_native_workload_rollout.native_visit_actions_v2_enabled || (
        local.effective_scribe_native_workload_rollout.enabled &&
        local.effective_scribe_native_workload_rollout.provider_context_enabled &&
        local.effective_scribe_native_workload_rollout.existing_patient_visits_enabled
      )
    )
  )

  production_live_effective_native_production_valid = (
    (
      !local.effective_scribe_native_production_rollout.capture_enabled || (
        local.effective_enable_scribe_workflow &&
        length(local.effective_scribe_native_production_rollout.capture_upload_origins) > 0
      )
      ) && (
      local.effective_scribe_native_production_rollout.capture_enabled || !anytrue([
        local.effective_scribe_native_production_rollout.capture_long_visits_enabled,
        local.effective_scribe_native_production_rollout.capture_notes_enabled,
        local.effective_scribe_native_production_rollout.capture_recovery_enabled,
        local.effective_scribe_native_production_rollout.capture_source_enabled,
        local.effective_scribe_native_production_rollout.capture_working_transcripts_enabled,
        local.effective_scribe_native_production_rollout.combined_session_preparation_enabled,
        local.effective_scribe_native_production_rollout.encounter_prep_enabled,
      ])
      ) && alltrue([
        for origin in local.effective_scribe_native_production_rollout.capture_upload_origins :
        can(regex("^https://[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+(:443)?$", origin))
    ])
  )

  production_live_effective_native_valid = (
    local.production_live_effective_native_workload_valid &&
    local.production_live_effective_native_production_valid &&
    (
      !local.effective_enable_scribe_workflow || (
        can(regex("^https://", local.effective_scribe_service_base_url)) &&
        can(regex("^api://.+/\\.default$", local.effective_scribe_service_scope)) &&
        local.effective_scribe_service_timeout_seconds >= 1 &&
        local.effective_scribe_service_timeout_seconds <= 60
      )
    )
  )

  production_live_effective_bundle_valid = (
    (
      !local.effective_scribe_core_bundle.api_enabled || try(
        jsondecode(local.effective_scribe_core_bundle.api_configuration_json).schema_version == 1 &&
        jsondecode(local.effective_scribe_core_bundle.receiver_configuration_json).authority_backend == "core-provider-context-v1" &&
        can(regex("^[0-9a-f]{64}$", local.effective_scribe_core_bundle.receiver_contract_sha256)),
        false,
      )
      ) && (
      !local.effective_scribe_core_bundle.captured_worker_enabled || try(
        local.effective_scribe_core_bundle.attach_existing_translation_identity &&
        jsondecode(local.effective_scribe_core_bundle.speech_configuration_json).schema_version == 1 &&
        jsondecode(local.effective_scribe_core_bundle.translation_configuration_json).schema_version == 1,
        false,
      )
    )
  )

  production_live_effective_settings_valid = (
    local.production_live_pin_inputs_remain_default &&
    local.production_live_effective_cancellation_valid &&
    local.production_live_effective_native_valid &&
    local.production_live_effective_bundle_valid
  )
}
