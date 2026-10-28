locals {
  # One reviewed switch drives every Core Session Prep receiver/runtime gate.
  # The provider frontend and Sovereign deployment remain separate release
  # artifacts and cannot be enabled by this object.
  session_prep_shared_public_values = {
    SESSION_PREP_PROVIDER_ENABLED        = tostring(var.session_prep_production_rollout.enabled)
    SESSION_PREP_PROVIDER_CLERK_SUBJECTS = join(",", sort(tolist(var.session_prep_production_rollout.provider_clerk_subjects)))
  }

  # The worker calls current_source_state(), which enforces the same Authority
  # Kernel completeness gate as the API. Keep this non-secret runtime subset
  # identical on both workloads; no Agent API identity or capability-bearing
  # record-grant secret is projected to the worker.
  session_prep_authority_runtime_public_values = {
    AUTHORITY_KERNEL_ENABLED                  = tostring(var.session_prep_production_rollout.enabled)
    MOUNT_CONTEXT_TOKEN_TTL_SECONDS           = "900"
    MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS = "300"
    MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS     = "28800"
  }

  session_prep_api_public_values = {
    AGENT_API_ENABLED              = tostring(var.session_prep_production_rollout.enabled)
    AGENT_API_AUDIENCE             = var.session_prep_production_rollout.agent_api_audience
    AGENT_API_SOVEREIGN_CLIENT_IDS = var.session_prep_production_rollout.sovereign_client_id
  }

  session_prep_api_controlled_env_names = toset(concat(
    keys(local.session_prep_shared_public_values),
    keys(local.session_prep_authority_runtime_public_values),
    keys(local.session_prep_api_public_values),
  ))
  session_prep_worker_controlled_env_names = toset(concat(
    keys(local.session_prep_shared_public_values),
    keys(local.session_prep_authority_runtime_public_values),
  ))
  session_prep_shared_controlled_env_names = toset(keys(local.session_prep_shared_public_values))

  session_prep_api_generic_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_api_only_secret_vars,
    var.python_billing_secret_vars,
    var.python_billing_api_only_secret_vars,
  )
  session_prep_worker_generic_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_billing_secret_vars,
  )
  session_prep_scheduler_generic_env_names = setunion(
    var.python_public_vars,
    var.python_secret_vars,
  )

  session_prep_api_env_name_collisions = setintersection(
    local.session_prep_api_controlled_env_names,
    local.session_prep_api_generic_env_names,
  )
  session_prep_worker_env_name_collisions = setintersection(
    local.session_prep_worker_controlled_env_names,
    local.session_prep_worker_generic_env_names,
  )
  session_prep_scheduler_env_name_collisions = setintersection(
    local.session_prep_shared_controlled_env_names,
    local.session_prep_scheduler_generic_env_names,
  )

  session_prep_clerk_issuer     = try(data.external.secrets.result["AUTHORITY_CLERK_ISSUER"], "")
  session_prep_record_grant_key = try(data.external.secrets.result["SOVEREIGN_RECORD_GRANT_SECRET"], "")

  # Closed syntax checks mirror the runtime contracts without placing either
  # value in an output or diagnostic. A Clerk issuer is one canonical HTTPS
  # DNS origin (no credentials, port, path, query, fragment, whitespace, or
  # trailing slash). A Fernet key is exactly 32 bytes in canonical URL-safe
  # base64; the restricted penultimate character enforces zero pad bits.
  session_prep_clerk_issuer_valid = (
    local.session_prep_clerk_issuer == trimspace(local.session_prep_clerk_issuer) &&
    can(regex(
      "^https://[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$",
      local.session_prep_clerk_issuer,
    ))
  )
  session_prep_record_grant_key_valid = (
    length(local.session_prep_record_grant_key) == 44 &&
    can(regex(
      "^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]=$",
      local.session_prep_record_grant_key,
    ))
  )
  session_prep_authority_runtime_prerequisites_valid = (
    !var.session_prep_production_rollout.enabled || local.session_prep_clerk_issuer_valid
  )
  session_prep_production_prerequisites_valid = (
    !var.session_prep_production_rollout.enabled || (
      local.session_prep_clerk_issuer_valid &&
      local.session_prep_record_grant_key_valid
    )
  )
}
