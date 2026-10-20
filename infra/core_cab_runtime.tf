# The exact source-admission document is API-only protected configuration.
# Existing deployment-owned identities are selected explicitly; none are created.
locals {
  core_cab_public_defaults = {
    CORE_CAB_AUTHORIZE_ENABLED          = "false"
    CORE_CAB_AUTHORIZE_TENANT_ID        = ""
    CORE_CAB_AUTHORIZE_AUDIENCE         = ""
    CORE_CAB_AUTHORIZE_CALLER_ID        = ""
    CORE_CAB_BRIDGE_ENABLED             = "false"
    CORE_CAB_ASYNC_ENABLED              = "false"
    CORE_CAB_CONTINUATION_ENABLED       = "false"
    CORE_CAB_ORIGIN                     = ""
    CORE_CAB_AUDIENCE                   = ""
    CORE_CAB_MANAGED_IDENTITY_CLIENT_ID = ""
  }
  core_cab_public_values = {
    for name, fallback in local.core_cab_public_defaults : name => try(data.external.secrets.result[name], fallback)
  }
  core_cab_policy = try(data.external.secrets.result["BILLING_SOURCE_POLICY_JSON"], "")
  core_cab_secret_values = local.core_cab_policy == "" ? {} : {
    BILLING_SOURCE_POLICY_JSON = local.core_cab_policy
  }
  core_cab_configuration_valid = (
    alltrue([for name in ["CORE_CAB_AUTHORIZE_ENABLED", "CORE_CAB_BRIDGE_ENABLED", "CORE_CAB_ASYNC_ENABLED", "CORE_CAB_CONTINUATION_ENABLED"] :
    contains(["true", "false"], local.core_cab_public_values[name])]) &&
    (local.core_cab_public_values.CORE_CAB_AUTHORIZE_ENABLED != "true" || alltrue([
      for name in ["CORE_CAB_AUTHORIZE_TENANT_ID", "CORE_CAB_AUTHORIZE_AUDIENCE", "CORE_CAB_AUTHORIZE_CALLER_ID"] :
      can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", local.core_cab_public_values[name]))
    ])) &&
    (local.core_cab_public_values.CORE_CAB_BRIDGE_ENABLED != "true" || (
      local.core_cab_public_values.CORE_CAB_AUTHORIZE_ENABLED == "true" &&
      alltrue([for name in ["CORE_CAB_AUDIENCE", "CORE_CAB_MANAGED_IDENTITY_CLIENT_ID"] :
      can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", local.core_cab_public_values[name]))]) &&
      can(regex("^https://[A-Za-z0-9.-]+(:443)?$", local.core_cab_public_values.CORE_CAB_ORIGIN))
    )) &&
    (local.core_cab_public_values.CORE_CAB_ASYNC_ENABLED != "true" ||
    local.core_cab_public_values.CORE_CAB_BRIDGE_ENABLED == "true") &&
    (local.core_cab_public_values.CORE_CAB_CONTINUATION_ENABLED != "true" ||
    local.core_cab_public_values.CORE_CAB_ASYNC_ENABLED == "true")
  )
}
