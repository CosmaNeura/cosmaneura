variable "subscription_id" {
  description = "Exact Azure subscription containing the staging rehearsal control resource group."
  type        = string
  default     = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.subscription_id))
    error_message = "subscription_id must be a lowercase UUID"
  }
}

variable "tenant_id" {
  description = "Exact Entra tenant for the staging-only identity."
  type        = string
  default     = "d51754d0-bba5-4810-820e-afac542844d6"

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.tenant_id))
    error_message = "tenant_id must be a lowercase UUID"
  }
}

variable "enable_sanitizer_bootstrap_identity" {
  description = "Create the authority-free, unfederated sanitizer bootstrap UAMI for one reviewed rehearsal lifecycle."
  type        = bool
  default     = false
}

variable "sanitizer_bootstrap_identity_plan_approved" {
  description = "Action-time approval that the exact saved plan creates only the reviewed UAMI and grants or attaches no authority."
  type        = bool
  default     = false
}

variable "sanitizer_bootstrap_rehearsal_id" {
  description = "Exact non-PHI rehearsal identifier tagged onto the bootstrap UAMI."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.sanitizer_bootstrap_rehearsal_id == null || can(regex("^[0-9a-f]{12}$", var.sanitizer_bootstrap_rehearsal_id))
    error_message = "sanitizer_bootstrap_rehearsal_id must be null or exactly 12 lowercase hexadecimal characters"
  }
}

variable "sanitizer_bootstrap_cleanup_not_after" {
  description = "Whole-second UTC deadline after which a saved plan must not create the bootstrap UAMI."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = var.sanitizer_bootstrap_cleanup_not_after == null || (
      can(regex("^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.sanitizer_bootstrap_cleanup_not_after)) &&
      can(timecmp(var.sanitizer_bootstrap_cleanup_not_after, var.sanitizer_bootstrap_cleanup_not_after))
    )
    error_message = "sanitizer_bootstrap_cleanup_not_after must be null or a valid whole-second UTC timestamp"
  }
}

variable "tags" {
  description = "Non-sensitive ownership tags applied to the staging-only bootstrap UAMI."
  type        = map(string)
  default = {
    environment         = "staging"
    managed-by          = "opentofu"
    purpose             = "staging-restore-sanitizer-bootstrap"
    data-classification = "non-phi-control-metadata"
  }
}
