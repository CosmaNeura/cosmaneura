locals {
  # These settings are intentionally separate from the generic 1Password-backed
  # environment projection. Their defaults make the production revisions dark,
  # and any activation must be an independently reviewed source change.
  scribe_native_api_public_values = {
    SCRIBE_NATIVE_WORKLOAD_ENABLED              = tostring(var.scribe_native_workload_rollout.enabled)
    SCRIBE_NATIVE_WORKLOAD_AUDIENCE             = var.scribe_native_workload_rollout.audience
    SCRIBE_NATIVE_WORKLOAD_CLIENT_ID            = var.scribe_native_workload_rollout.client_id
    SCRIBE_CAPTURE_ENABLED                      = tostring(var.scribe_native_production_rollout.capture_enabled)
    SCRIBE_CAPTURE_LONG_VISITS_ENABLED          = tostring(var.scribe_native_production_rollout.capture_long_visits_enabled)
    SCRIBE_CAPTURE_NOTES_ENABLED                = tostring(var.scribe_native_production_rollout.capture_notes_enabled)
    SCRIBE_CAPTURE_RECOVERY_ENABLED             = tostring(var.scribe_native_production_rollout.capture_recovery_enabled)
    SCRIBE_CAPTURE_SOURCE_ENABLED               = tostring(var.scribe_native_production_rollout.capture_source_enabled)
    SCRIBE_CAPTURE_UPLOAD_ORIGINS               = join(",", sort(tolist(var.scribe_native_production_rollout.capture_upload_origins)))
    SCRIBE_CAPTURE_WORKING_TRANSCRIPTS_ENABLED  = tostring(var.scribe_native_production_rollout.capture_working_transcripts_enabled)
    SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED = tostring(var.scribe_native_production_rollout.combined_session_preparation_enabled)
    SCRIBE_ENCOUNTER_CREATION_ENABLED           = tostring(var.scribe_native_production_rollout.encounter_creation_enabled)
    SCRIBE_ENCOUNTER_PREP_ENABLED               = tostring(var.scribe_native_production_rollout.encounter_prep_enabled)
    ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED       = tostring(var.scribe_native_production_rollout.zoom_durable_capture_bindings_enabled)
  }

  # The durable Zoom adapter is imported by HTTP callback handling and queued
  # work. Keep its cutover value identical on all three long-lived workloads,
  # as required by the existing drained-cutover flight plan. Native device and
  # encounter gates remain API-only.
  zoom_durable_public_values = {
    ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED = tostring(var.scribe_native_production_rollout.zoom_durable_capture_bindings_enabled)
  }

  # A rollout name must have one source of truth. Keep the exact workload
  # projections here beside the source-controlled values so adding another
  # generic environment set cannot silently reintroduce duplicate entries.
  scribe_native_controlled_env_names = toset(keys(local.scribe_native_api_public_values))
  scribe_native_workload_prerequisites_valid = (
    !var.scribe_native_workload_rollout.enabled || local.session_prep_clerk_issuer_valid
  )

  scribe_native_api_generic_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_api_only_secret_vars,
    var.python_billing_secret_vars,
    var.python_billing_api_only_secret_vars,
  )
  scribe_native_worker_generic_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_billing_secret_vars,
  )
  scribe_native_scheduler_generic_env_names = setunion(
    var.python_public_vars,
    var.python_secret_vars,
  )

  scribe_native_api_env_name_collisions = setintersection(
    local.scribe_native_controlled_env_names,
    local.scribe_native_api_generic_env_names,
  )
  scribe_native_worker_env_name_collisions = setintersection(
    local.scribe_native_controlled_env_names,
    local.scribe_native_worker_generic_env_names,
  )
  scribe_native_scheduler_env_name_collisions = setintersection(
    local.scribe_native_controlled_env_names,
    local.scribe_native_scheduler_generic_env_names,
  )
}
