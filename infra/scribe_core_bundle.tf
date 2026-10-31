variable "scribe_core_bundle" {
  description = "Default-off source hooks in the existing Core API/worker/scheduler only. Existing-identity attachment and all activation remain separate owner-reviewed mutations; no new resource or grant is created."
  type = object({
    api_enabled                          = optional(bool, false)
    captured_worker_enabled              = optional(bool, false)
    attach_existing_translation_identity = optional(bool, false)
    api_configuration_json               = optional(string, "")
    receiver_configuration_json          = optional(string, "")
    receiver_contract_sha256             = optional(string, "")
    speech_configuration_json            = optional(string, "")
    translation_configuration_json       = optional(string, "")
  })
  default = {}

  validation {
    condition = !var.scribe_core_bundle.api_enabled || try(
      jsondecode(var.scribe_core_bundle.api_configuration_json).schema_version == 1 &&
      jsondecode(var.scribe_core_bundle.receiver_configuration_json).authority_backend == "core-provider-context-v1" &&
      can(regex("^[0-9a-f]{64}$", var.scribe_core_bundle.receiver_contract_sha256)), false,
    )
    error_message = "the API bundle requires closed Scribe configuration, the Core-context receiver backend and exact sealed manifest digest"
  }
  validation {
    condition = !var.scribe_core_bundle.captured_worker_enabled || try(
      var.scribe_core_bundle.attach_existing_translation_identity &&
      jsondecode(var.scribe_core_bundle.speech_configuration_json).schema_version == 1 &&
      jsondecode(var.scribe_core_bundle.translation_configuration_json).schema_version == 1, false,
    )
    error_message = "captured pickup requires the separately prepared existing translation identity and both closed stage configurations"
  }
}

data "azurerm_user_assigned_identity" "scribe_bundle_translation" {
  count               = var.scribe_core_bundle.attach_existing_translation_identity ? 1 : 0
  name                = "cosmaneura-scribe-production-runtime"
  resource_group_name = "cosmaneura-scribe-production"
  lifecycle {
    postcondition {
      condition     = lower(self.client_id) == "39d00a14-ca79-4cbf-8821-3e2af90ea0e2" && lower(self.principal_id) == "3f712a80-f393-49d0-ab42-6a40bc50dfbd"
      error_message = "only the exact already-existing non-admin foundation runtime identity may be selected"
    }
  }
}

locals {
  scribe_bundle_api_values = merge({
    SCRIBE_BUNDLE_API_ENABLED = tostring(var.scribe_core_bundle.api_enabled)
    }, var.scribe_core_bundle.api_enabled ? {
    SCRIBE_BUNDLE_API_CONFIGURATION_JSON             = var.scribe_core_bundle.api_configuration_json
    COSMANEURA_SCRIBE_PROVIDER_CONFIGURATION_JSON    = var.scribe_core_bundle.receiver_configuration_json
    COSMANEURA_SCRIBE_PROVIDER_IMAGE_CONTRACT_SHA256 = var.scribe_core_bundle.receiver_contract_sha256
  } : {})
  scribe_bundle_worker_values = merge({
    SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED = tostring(var.scribe_core_bundle.captured_worker_enabled)
    }, var.scribe_core_bundle.captured_worker_enabled ? {
    SCRIBE_BUNDLE_SPEECH_CONFIGURATION_JSON      = var.scribe_core_bundle.speech_configuration_json
    SCRIBE_BUNDLE_TRANSLATION_CONFIGURATION_JSON = var.scribe_core_bundle.translation_configuration_json
  } : {})
  scribe_bundle_scheduler_values = {
    SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED = tostring(var.scribe_core_bundle.captured_worker_enabled)
  }
  scribe_bundle_controlled_names = toset([
    "SCRIBE_BUNDLE_API_ENABLED", "SCRIBE_BUNDLE_CAPTURED_WORKER_ENABLED",
    "SCRIBE_BUNDLE_API_CONFIGURATION_JSON", "COSMANEURA_SCRIBE_PROVIDER_CONFIGURATION_JSON",
    "COSMANEURA_SCRIBE_PROVIDER_IMAGE_CONTRACT_SHA256", "SCRIBE_BUNDLE_SPEECH_CONFIGURATION_JSON",
    "SCRIBE_BUNDLE_TRANSLATION_CONFIGURATION_JSON",
  ])
}
