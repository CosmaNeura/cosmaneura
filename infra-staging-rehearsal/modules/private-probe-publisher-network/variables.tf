variable "enabled" {
  description = "Create this separate network only after exact reviewed human plan approval. The staging root does not instantiate this module."
  type        = bool
  default     = false
}

variable "exact_network_plan_approved" {
  description = "Action-time approval of exact resource, subnet, service, egress and recurring cost choices; not publication or execution authority."
  type        = bool
  default     = false
}

variable "github_organization_database_id" {
  description = "Independently read back organization database ID, not its display name."
  type        = string
  default     = ""
  nullable    = false

  validation {
    condition     = var.github_organization_database_id == "" || can(regex("^[1-9][0-9]{0,19}$", var.github_organization_database_id))
    error_message = "The organization database ID must be an exact positive decimal string"
  }
}

variable "registry_resource_id" {
  description = "Exact existing private ACR resource ID. This module does not alter registry properties, identities, grants or contents."
  type        = string
  default     = ""
  nullable    = false

  validation {
    condition = var.registry_resource_id == "" || can(regex(
      "^/subscriptions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/resourceGroups/[a-zA-Z0-9._-]+/providers/Microsoft[.]ContainerRegistry/registries/[a-z0-9]{5,50}$",
      var.registry_resource_id
    ))
    error_message = "The registry resource ID must be an exact ARM ACR ID, not a URL, name or wildcard"
  }
}

variable "reviewed_https_domains" {
  description = "Exact independently reviewed GitHub Actions/control-plane hostnames; no wildcards, IP ranges or automatic meta-endpoint expansion. Refresh the reviewed list when requirements change."
  type        = set(string)
  default     = []
  nullable    = false

  validation {
    condition = length(var.reviewed_https_domains) <= 256 && alltrue([
      for domain in var.reviewed_https_domains :
      length(domain) <= 253 &&
      alltrue([for label in split(".", domain) : length(label) <= 63]) &&
      !can(regex("^[0-9]+([.][0-9]+){3}$", domain)) &&
      can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", domain))
    ])
    error_message = "HTTPS destinations must be at most256 exact lowercase DNS names, without wildcards or URLs"
  }
}

variable "tags" {
  type = map(string)
  default = {
    environment = "staging"
    purpose     = "synthetic-probe-publisher-network"
  }
}
