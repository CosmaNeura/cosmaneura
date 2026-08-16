variable "tags" {
  description = "A map of tags to add to all resources"
  type        = map(string)

  default = {
    environment = "staging"
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
    "CLERK_PRIVATE_KEY", "SENTRY_DSN", "ASSEMBLY_API_KEY", "SMTP_URL",
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
    # recall.ai region
    "RECALL_API_BASE",
    # explicit cookie domain to avoid using internal Azure Container App domains
    "COOKIE_DOMAIN",
    # app-only Entra authentication for the feature-flagged Scribe workload route
    "SCRIBE_HANDOFF_ENABLED", "ENTRA_TENANT_ID", "SCRIBE_API_AUDIENCE",
    "SCRIBE_CALLER_CLIENT_ID", "SCRIBE_RECONCILER_CALLER_CLIENT_ID"
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
