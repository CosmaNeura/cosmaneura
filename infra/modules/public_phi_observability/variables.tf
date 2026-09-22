variable "environment" {
  description = "Environment label included in every alert."
  type        = string
}

variable "resource_prefix" {
  description = "Stable prefix for Azure Monitor resource names."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that owns the monitored services."
  type        = string
}

variable "resource_group_id" {
  description = "Exact resource group ID used to scope administrative-failure signals."
  type        = string
}

variable "location" {
  description = "Azure region used for regional scheduled-query alert resources."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Workspace containing Container Apps structured logs."
  type        = string
}

variable "postgresql_server_id" {
  description = "Exact PostgreSQL Flexible Server resource ID."
  type        = string
}

variable "redis_cache_id" {
  description = "Exact Azure Cache for Redis resource ID."
  type        = string
}

variable "upload_storage_account_id" {
  description = "Exact uploads Storage Account resource ID."
  type        = string
}

variable "alert_email" {
  description = "Role mailbox for the public-PHI operations action group. Never commit a personal address."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email))
    error_message = "alert_email must be a valid role mailbox"
  }
}

variable "support_owner" {
  description = "Non-personal support-owner role slug recorded on alerts."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,62}$", var.support_owner))
    error_message = "support_owner must be a 3-63 character lowercase role slug"
  }
}

variable "tags" {
  description = "Tags inherited from the environment."
  type        = map(string)
  default     = {}
}
