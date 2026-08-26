variable "tags" {
  description = "A map of tags to add to all resources"
  type        = map(string)

  default = {
    environment = "production"
    managed     = true
  }
}

variable "onepassword_tags" {
  description = "A map of tags to add to all opentofu-generated 1p items"
  type        = list(string)

  default = [
    "terraform-managed"
  ]
}

# variables which should be exposed publicly in the azure ui
variable "python_secret_vars" {
  description = "ENV vars which need to remain secret"
  type        = set(string)
  default = [
    "DATABASE_URL", "REDIS_URL",
    "SESSION_SECRET_KEY",
    # fernet key for encrypting oauth tokens at rest (doctor_video_integration)
    "ENCRYPTION_KEY",
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

# Secrets used only by the HTTP API. Keep capability-bearing values out of
# worker, scheduler, and migration workloads.
variable "python_api_only_secret_vars" {
  description = "Secret ENV vars exposed only to the API Container App"
  type        = set(string)
  default = [
    "SOVEREIGN_RECORD_GRANT_SECRET",
    "RECALL_WEBHOOK_SECRET",
  ]
}

# Stripe's maintenance key is needed by the API and worker, while the webhook
# signing secret is API-only. Neither capability belongs in migrations,
# scheduler.
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
    # zoom oauth redirects
    "ZOOM_REDIRECT_URI", "ZOOM_SUCCESS_REDIRECT",
    # public origin of this backend, used to build the zak_url that Recall
    # bots fetch on join
    "API_BASE_URL", "RECALL_API_BASE",
    # app-only Entra authentication for the feature-flagged Scribe workload route
    "SCRIBE_HANDOFF_ENABLED", "ENTRA_TENANT_ID", "SCRIBE_API_AUDIENCE",
    "SCRIBE_CALLER_CLIENT_ID", "SCRIBE_RECONCILER_CALLER_CLIENT_ID",
    # capability authorization remains independent of Authority Kernel rollout
    "AUTHORITY_CLERK_ISSUER",
    "DURABLE_WEBHOOK_INBOX_ENABLED"
  ]
}

variable "enable_scribe_workflow" {
  description = "Enable passwordless calls from the platform web API to the private Scribe API."
  type        = bool
  default     = false
}

variable "scribe_service_base_url" {
  description = "Private HTTPS origin of the Scribe API; contains no credential."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_scribe_workflow ||
      can(regex("^https://", var.scribe_service_base_url))
    )
    error_message = "scribe_service_base_url must be HTTPS when Scribe workflow calls are enabled"
  }
}

variable "scribe_service_scope" {
  description = "Scribe Entra API scope in api://<app-id>/.default form; contains no credential."
  type        = string
  default     = ""

  validation {
    condition = (
      !var.enable_scribe_workflow ||
      can(regex("^api://.+/\\.default$", var.scribe_service_scope))
    )
    error_message = "scribe_service_scope must use api://<app-id>/.default when enabled"
  }
}

variable "scribe_service_timeout_seconds" {
  description = "Timeout for one platform-to-Scribe API request."
  type        = number
  default     = 15

  validation {
    condition     = var.scribe_service_timeout_seconds >= 1 && var.scribe_service_timeout_seconds <= 60
    error_message = "scribe_service_timeout_seconds must be between 1 and 60"
  }
}

variable "deploy_parallel_vnet_environment" {
  description = "Create the empty East US VNet-integrated platform foundation after a separately approved plan."
  type        = bool
  default     = false
}

variable "parallel_platform_location" {
  description = "Target region for the parallel platform network and Container Apps environment."
  type        = string
  default     = "eastus"
}

variable "parallel_platform_vnet_address_space" {
  description = "Non-overlapping address space for the parallel platform VNet."
  type        = list(string)
  default     = ["10.43.0.0/16"]

  validation {
    condition     = length(var.parallel_platform_vnet_address_space) > 0 && alltrue([for prefix in var.parallel_platform_vnet_address_space : can(cidrnetmask(prefix))])
    error_message = "parallel_platform_vnet_address_space must contain valid CIDR prefixes"
  }
}

variable "parallel_platform_container_apps_subnet_address_prefixes" {
  description = "Dedicated subnet prefixes for the parallel Container Apps workload-profile environment."
  type        = list(string)
  default     = ["10.43.2.0/23"]

  validation {
    condition     = length(var.parallel_platform_container_apps_subnet_address_prefixes) > 0 && alltrue([for prefix in var.parallel_platform_container_apps_subnet_address_prefixes : can(cidrnetmask(prefix))])
    error_message = "parallel_platform_container_apps_subnet_address_prefixes must contain valid CIDR prefixes"
  }
}

variable "scribe_virtual_network_id" {
  description = "Resource ID of the Scribe VNet to peer for an approved synthetic connectivity phase."
  type        = string
  default     = ""

  validation {
    condition = (
      var.scribe_virtual_network_id == "" ||
      can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.Network/virtualNetworks/[^/]+$", var.scribe_virtual_network_id))
    )
    error_message = "scribe_virtual_network_id must be an Azure virtual network resource ID"
  }
}

variable "deploy_parallel_private_data_endpoints" {
  description = "Add private endpoints from the parallel East US VNet to the current PostgreSQL, Redis, and uploads storage after separate review."
  type        = bool
  default     = false
}

variable "parallel_platform_private_endpoint_subnet_address_prefixes" {
  description = "Dedicated subnet prefixes for cross-region private endpoints used during migration."
  type        = list(string)
  default     = ["10.43.1.0/24"]

  validation {
    condition     = length(var.parallel_platform_private_endpoint_subnet_address_prefixes) > 0 && alltrue([for prefix in var.parallel_platform_private_endpoint_subnet_address_prefixes : can(cidrnetmask(prefix))])
    error_message = "parallel_platform_private_endpoint_subnet_address_prefixes must contain valid CIDR prefixes"
  }
}

variable "deploy_parallel_network_smoke_job" {
  description = "Create a manual, scale-to-zero synthetic job in the parallel environment after its image and private network are approved."
  type        = bool
  default     = false
}

variable "parallel_network_smoke_image_repository" {
  description = "Repository path in the existing ACR for the synthetic network probe image."
  type        = string
  default     = "cosmaneura/platform-network-smoke"
}

variable "parallel_network_smoke_image_tag" {
  description = "Immutable commit SHA for the synthetic network probe image."
  type        = string
  default     = "not-built"

  validation {
    condition     = var.parallel_network_smoke_image_tag == "not-built" || can(regex("^[0-9a-f]{7,64}$", var.parallel_network_smoke_image_tag))
    error_message = "parallel_network_smoke_image_tag must be not-built or a lowercase hexadecimal commit SHA"
  }
}

variable "parallel_scribe_private_base_url" {
  description = "Private HTTPS Scribe origin used only by the synthetic network probe; contains no credential."
  type        = string
  default     = ""

  validation {
    condition = (
      var.parallel_scribe_private_base_url == "" ||
      can(regex("^https://[^/]+/?$", var.parallel_scribe_private_base_url))
    )
    error_message = "parallel_scribe_private_base_url must be an HTTPS origin without a path"
  }
}

# start commands for the container types
data "external" "procfile" {
  program     = ["yq", "Procfile", "--output-format", "json"]
  working_dir = "${path.module}/.."
}

# contains all of the production secrets required for container configuration
# use `data.external.secrets.result[KEY]` to access this data
data "external" "secrets" {
  # Run `just direnv_export .env.production.backend` to inspect results
  program     = ["just", "direnv_export", ".env.production.backend"]
  working_dir = "${path.module}/.."
}

# This read-only guard makes the selector an adoption mechanism, never a bulk
# cutover mechanism.  Phase A may add the unused v2 value while all workloads
# still select database-url.  Selecting v2 is accepted only after the staged
# operator has already moved all four live workloads and retained the old
# rollback secret.
data "external" "database_url_live_guard" {
  program = ["python", "${path.module}/database_url_live_guard.py"]
  query = {
    resource_group  = azurerm_resource_group.main.name
    selected_secret = local.database_url_secret_name
    subscription_id = local.azure_subscription_id
    v2_enabled      = local.database_url_v2_enabled_text
  }
}
