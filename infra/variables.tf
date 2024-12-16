# variable "GH_GHCR_TOKEN" {
#   description = "The GitHub Container Registry token"
#   sensitive   = true
# }

variable "tags" {
  description = "A map of tags to add to all resources"
  type        = map(string)

  default = {
    environment = "production"
    managed     = true
  }
}

# use `data.external.secrets.result[KEY]` to access this data
data "external" "secrets" {
  # Run `just direnv_export .env.production.backend` to inspect results
  program     = ["just", "direnv_export", ".env.production.backend"]
  working_dir = "${path.module}/.."
}

# TODO this is not ideal, should be a better way to do this

# data "external" "IPINFO_API_KEY" {
#   program = ["${path.module}/bin/op_read.sh", "op://Knolbe/IPinfo/api"]
# }

# data "external" "OPENAI_API_KEY" {
#   program = ["${path.module}/bin/op_read.sh", "op://Knolbe/cosmaneura OpenAI Key/credential"]
# }

# data "external" "SENTRY_DSN" {
#   program = ["${path.module}/bin/op_read.sh", "op://cosmaneura/Sentry/credentials"]
# }

# data "external" "RADAR_API_KEY" {
#   program = ["${path.module}/bin/op_read.sh", "op://Knolbe/Radar API Key/credential"]
# }
