# Source-controlled Scribe cancellation and Core→Scribe workflow client flags.
# Variable defaults stay dark so provider-mocked tests remain additive-safe.
# Production live values come from production_live_settings.tf when
# use_production_live_settings_pin=true (the default).

variable "scribe_cancellation_production_rollout" {
  description = "Production Scribe cancellation intake/policy and shared ledger fence. Defaults are dark; the production live settings pin projects live values when enabled."
  type = object({
    ledger_ready  = bool
    enabled       = bool
    grace_days    = number
    phi_scope     = string
    policy_sha256 = string
  })
  nullable = false
  default = {
    ledger_ready  = false
    enabled       = false
    grace_days    = 90
    phi_scope     = "core-and-scribe-clinical-records"
    policy_sha256 = ""
  }

  validation {
    condition = !var.scribe_cancellation_production_rollout.enabled || (
      var.scribe_cancellation_production_rollout.ledger_ready &&
      var.scribe_cancellation_production_rollout.grace_days >= 30 &&
      var.scribe_cancellation_production_rollout.grace_days <= 90 &&
      tostring(var.scribe_cancellation_production_rollout.grace_days) == tostring(floor(var.scribe_cancellation_production_rollout.grace_days)) &&
      var.scribe_cancellation_production_rollout.phi_scope == "core-and-scribe-clinical-records" &&
      can(regex("^[0-9a-f]{64}$", var.scribe_cancellation_production_rollout.policy_sha256))
    )
    error_message = "cancellation intake requires ledger_ready, grace_days 30-90 without leading zeros, the exact PHI scope, and a lowercase 64-hex policy digest"
  }

  validation {
    condition     = var.scribe_cancellation_production_rollout.ledger_ready || !var.scribe_cancellation_production_rollout.enabled
    error_message = "cancellation intake cannot enable without the migrated ledger fence"
  }
}

variable "scribe_workflow_client_production_rollout" {
  description = "API-only Core→Scribe workflow client capability flags. Defaults are dark; the production live settings pin projects live values when enabled."
  type = object({
    identity_reuse_enabled = bool
    http_pool_enabled      = bool
  })
  nullable = false
  default = {
    identity_reuse_enabled = false
    http_pool_enabled      = false
  }
}

locals {
  # Ledger readiness fences Doctor access on every long-lived workload.
  scribe_cancellation_shared_public_values = {
    SCRIBE_CANCELLATION_LEDGER_READY = tostring(local.effective_scribe_cancellation_production_rollout.ledger_ready)
  }

  # Intake and reviewed policy stay API-only.
  scribe_cancellation_api_public_values = merge(
    local.scribe_cancellation_shared_public_values,
    local.effective_scribe_cancellation_production_rollout.enabled ? {
      SCRIBE_CANCELLATION_ENABLED       = "true"
      SCRIBE_CANCELLATION_GRACE_DAYS    = tostring(local.effective_scribe_cancellation_production_rollout.grace_days)
      SCRIBE_CANCELLATION_PHI_SCOPE     = local.effective_scribe_cancellation_production_rollout.phi_scope
      SCRIBE_CANCELLATION_POLICY_SHA256 = local.effective_scribe_cancellation_production_rollout.policy_sha256
      } : {
      SCRIBE_CANCELLATION_ENABLED = "false"
    },
  )

  scribe_workflow_client_api_public_values = {
    SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED = tostring(local.effective_scribe_workflow_client_production_rollout.identity_reuse_enabled)
    SCRIBE_WORKFLOW_HTTP_POOL_ENABLED      = tostring(local.effective_scribe_workflow_client_production_rollout.http_pool_enabled)
  }

  scribe_cancellation_controlled_env_names = toset(concat(
    keys(local.scribe_cancellation_api_public_values),
    keys(local.scribe_cancellation_shared_public_values),
    keys(local.scribe_workflow_client_api_public_values),
  ))

  scribe_cancellation_api_generic_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_api_only_secret_vars,
    var.python_billing_secret_vars,
    var.python_billing_api_only_secret_vars,
  )
  scribe_cancellation_worker_generic_env_names = setunion(
    var.python_public_vars,
    var.python_billing_public_vars,
    var.python_secret_vars,
    var.python_billing_secret_vars,
  )
  scribe_cancellation_scheduler_generic_env_names = setunion(
    var.python_public_vars,
    var.python_secret_vars,
  )

  scribe_cancellation_api_env_name_collisions = setintersection(
    local.scribe_cancellation_controlled_env_names,
    local.scribe_cancellation_api_generic_env_names,
  )
  scribe_cancellation_worker_env_name_collisions = setintersection(
    toset(keys(local.scribe_cancellation_shared_public_values)),
    local.scribe_cancellation_worker_generic_env_names,
  )
  scribe_cancellation_scheduler_env_name_collisions = setintersection(
    toset(keys(local.scribe_cancellation_shared_public_values)),
    local.scribe_cancellation_scheduler_generic_env_names,
  )
}
