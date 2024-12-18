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
  default     = ["DATABASE_URL", "REDIS_URL", "CLERK_PRIVATE_KEY", "OPENAI_API_KEY", "SENTRY_DSN", "SMTP_URL"]
}

# container configuration which does not need to be secret
variable "python_public_vars" {
  description = "ENV vars which can be public"
  type        = set(string)
  default     = ["LOG_LEVEL", "EMAIL_FROM_ADDRESS", "PORT"]
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
