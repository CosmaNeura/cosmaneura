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

variable "sanitizer_bootstrap_lifetime_hours" {
  description = "Whole-hour bootstrap lifetime from the exact plan timestamp; limited to an integer from 1 through 24."
  type        = number
  default     = null
  nullable    = true

  validation {
    condition = var.sanitizer_bootstrap_lifetime_hours == null ? true : (
      var.sanitizer_bootstrap_lifetime_hours >= 1 &&
      var.sanitizer_bootstrap_lifetime_hours <= 24 &&
      floor(var.sanitizer_bootstrap_lifetime_hours) == var.sanitizer_bootstrap_lifetime_hours
    )
    error_message = "sanitizer_bootstrap_lifetime_hours must be null or an integer from 1 through 24"
  }
}
