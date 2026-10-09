variable "tags" {
  description = "A map of tags to add to all resources"
  type        = map(string)

  default = {
    environment = "staging"
    managed     = true
  }
}

variable "enable_public_phi_observability" {
  description = "Create PHI-free public-pilot metrics, alerts, diagnostics, and routing after staging approval."
  type        = bool
  default     = false
}

variable "public_phi_alert_email" {
  description = "Approved role mailbox for public-PHI alerts; required only when observability is enabled."
  type        = string
  default     = ""
  sensitive   = true

  validation {
    condition     = var.public_phi_alert_email == "" || can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.public_phi_alert_email))
    error_message = "public_phi_alert_email must be empty or a valid role mailbox"
  }
}

variable "public_phi_support_owner" {
  description = "Approved non-personal support-owner role slug; required only when observability is enabled."
  type        = string
  default     = ""

  validation {
    condition     = var.public_phi_support_owner == "" || can(regex("^[a-z0-9][a-z0-9-]{2,62}$", var.public_phi_support_owner))
    error_message = "public_phi_support_owner must be empty or a 3-63 character lowercase role slug"
  }
}

variable "onepassword_tags" {
  description = "A map of tags to add to all opentofu-generated 1p items"
  type        = list(string)

  default = [
    "terraform-managed"
  ]
}

variable "staging_container_apps_egress_ipv4" {
  description = "Operator-supplied outbound IPv4 address of the staging Container Apps; required for each plan and checked against refreshed scheduler state."
  type        = string

  validation {
    condition = (
      can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.staging_container_apps_egress_ipv4)) &&
      can(cidrhost("${var.staging_container_apps_egress_ipv4}/32", 0))
    )
    error_message = "staging_container_apps_egress_ipv4 must be a valid IPv4 address"
  }
}

# variables which should be exposed publicly in the azure ui
variable "python_secret_vars" {
  description = "ENV vars which need to remain secret"
  type        = set(string)
  default = [
    "DATABASE_URL", "REDIS_URL",
    "SESSION_SECRET_KEY", "ENCRYPTION_KEY",
    "CLERK_PRIVATE_KEY",
    "SENTRY_DSN", "ASSEMBLY_API_KEY", "SMTP_URL",
    # azure openai secret
    "AZURE_OPENAI_API_KEY",
    # azure upload
    "AZURE_UPLOADS_ACCOUNT_NAME", "AZURE_UPLOADS_ACCOUNT_KEY", "AZURE_UPLOADS_ACCOUNT_URL",
    # azure de-identification secrets
    "AZURE_DEID_TENANT_ID", "AZURE_DEID_CLIENT_ID", "AZURE_DEID_CLIENT_SECRET", "AZURE_DEID_ENDPOINT",
    # recall.ai
    "RECALL_API_KEY",
    # zoom oauth
    "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET", "ZOOM_SECRET_TOKEN"
  ]
}

# Secrets used only by the HTTP API. Staging's current Container Apps are
# configured by deploy.sh, which enforces this same split explicitly.
variable "python_api_only_secret_vars" {
  description = "Secret ENV vars exposed only to the API Container App"
  type        = set(string)
  default = [
    "SOVEREIGN_RECORD_GRANT_SECRET",
    "RECALL_WEBHOOK_SECRET",
  ]
}

variable "python_billing_secret_vars" {
  description = "Stripe secret ENV vars exposed only to API and worker"
  type        = set(string)
  default = [
    "STRIPE_SECRET_KEY",
  ]
}

variable "python_billing_api_only_secret_vars" {
  description = "Stripe secret ENV vars exposed only to the API"
  type        = set(string)
  default = [
    "STRIPE_WEBHOOK_SECRET",
  ]
}

variable "python_billing_public_vars" {
  description = "Non-secret Stripe configuration exposed only to API and worker"
  type        = set(string)
  default = [
    "STRIPE_INTEGRATION_ENABLED", "STRIPE_BILLING_ENABLED", "STRIPE_ACCOUNT_ID",
    "STRIPE_PRICE_ID", "STRIPE_API_VERSION", "STRIPE_LIVE_MODE",
    "STRIPE_PORTAL_CONFIGURATION_ID",
  ]
}


# container configuration which does not need to be secret
variable "python_public_vars" {
  description = "ENV vars which can be public"
  type        = set(string)
  default = [
    "LOG_LEVEL", "EMAIL_FROM_ADDRESS", "PORT", "ALLOWED_HOST_LIST", "INTAKE_CHAT_DOMAIN",
    # part of azure openai configuration
    "AZURE_OPENAI_ENDPOINT",
    # upload container names
    "AZURE_UPLOADS_CONTAINER_NAME", "AZURE_UPLOADS_EXAMPLE_NOTES_CONTAINER_NAME",
    # zoom oauth redirects and the source-controlled watchdog rollout gate
    "ZOOM_REDIRECT_URI", "ZOOM_SUCCESS_REDIRECT", "ZOOM_WATCHDOG_ENABLED",
    # public origin of this backend plus the recall.ai region
    "API_BASE_URL", "RECALL_API_BASE",
    # explicit cookie domain to avoid using internal Azure Container App domains
    "COOKIE_DOMAIN",
    # app-only Entra authentication for the feature-flagged Scribe workload route
    "SCRIBE_HANDOFF_ENABLED", "ENTRA_TENANT_ID", "SCRIBE_API_AUDIENCE",
    "SCRIBE_CALLER_CLIENT_ID", "SCRIBE_RECONCILER_CALLER_CLIENT_ID",
    # provider-first Agent API; exact one-provider allowlist excludes public
    "AGENT_API_ENABLED", "AGENT_API_AUDIENCE", "AGENT_API_SOVEREIGN_CLIENT_IDS",
    "AUTHORITY_KERNEL_ENABLED", "AUTHORITY_CLERK_ISSUER", "MOUNT_CONTEXT_TOKEN_TTL_SECONDS",
    "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS", "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS",
    "DURABLE_WEBHOOK_INBOX_ENABLED", "SENTRY_STAGING_ENABLED"
  ]
}

# start commands for the container types
data "external" "procfile" {
  program     = ["yq", "Procfile", "--output-format", "json"]
  working_dir = "${path.module}/.."
}

# contains all of the staging secrets required for container configuration
# use `data.external.secrets.result[KEY]` to access this data
data "external" "secrets" {
  # Run `just direnv_export .env.staging.backend` to inspect results
  program     = ["just", "direnv_export", ".env.staging.backend"]
  working_dir = "${path.module}/.."
}
