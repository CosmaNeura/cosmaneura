variable "subscription_id" {
  description = "Azure subscription containing the synthetic staging-rehearsal control plane."
  type        = string
  default     = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.subscription_id))
    error_message = "subscription_id must be a lowercase UUID"
  }
}

variable "tenant_id" {
  description = "Exact Entra tenant for the staging-only identities."
  type        = string
  default     = "d51754d0-bba5-4810-820e-afac542844d6"

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.tenant_id))
    error_message = "tenant_id must be a lowercase UUID"
  }
}

variable "deploy_staging_restore_control_plane" {
  description = "Create the reversible staging-rehearsal control-plane prerequisites after a separately reviewed plan."
  type        = bool
  default     = false
}

variable "target_cidr_inventory_revalidated" {
  description = "Action-time confirmation that 10.75.0.0/24 remains unused and non-overlapping across every connected Azure network."
  type        = bool
  default     = false
}

variable "control_cidr_inventory_revalidated" {
  description = "Action-time confirmation that 10.76.0.0/24 remains unused and non-overlapping across every connected Azure network."
  type        = bool
  default     = false
}

variable "enable_marker_create_assignments" {
  description = "Assign marker-create roles to unfederated job UAMIs only after live provider-fidelity testing proves the exact minimum data actions."
  type        = bool
  default     = false
}

variable "marker_create_authorization_proven" {
  description = "Action-time attestation that live non-PHI tests proved the exact conditional BlockBlob create/read data actions."
  type        = bool
  default     = false
}

variable "marker_create_data_actions" {
  description = "Exact reviewed Azure Blob data actions proven sufficient for conditional BlockBlob creation plus readback."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for action in var.marker_create_data_actions : contains([
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action",
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
      ], action)
    ])
    error_message = "marker_create_data_actions may contain only the reviewed Blob add, read, or write data actions"
  }
}

variable "irreversible_marker_controls_approved" {
  description = "Action-time approval for one-way marker retention controls. This is never implied by deploying the reversible control plane."
  type        = bool
  default     = false
}

variable "enable_marker_legal_holds" {
  description = "Set the restorecontrolactive legal hold through an explicit AzAPI action. Removing this flag does not clear a live hold."
  type        = bool
  default     = false
}

variable "lock_marker_immutability_policies" {
  description = "Permanently lock each marker container's 90-day WORM policy. A locked policy cannot be unlocked or deleted."
  type        = bool
  default     = false
}

variable "enable_marker_readonly_lock" {
  description = "Add a final ReadOnly lock only after legal holds, locked WORM, marker assignments, private execution, and trusted evidence are proven."
  type        = bool
  default     = false
}

variable "marker_storage_account_name" {
  description = "Globally unique name reserved for the non-PHI restore marker account. Revalidate availability before apply."
  type        = string
  default     = "cosmaneurarehearsalctl"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.marker_storage_account_name))
    error_message = "marker_storage_account_name must be 3-24 lowercase alphanumeric characters"
  }
}

variable "tags" {
  description = "Non-sensitive ownership tags applied to persistent rehearsal control-plane resources."
  type        = map(string)
  default = {
    environment         = "staging"
    managed-by          = "opentofu"
    purpose             = "staging-restore-rehearsal-control"
    data-classification = "non-phi-control-metadata"
  }
}
