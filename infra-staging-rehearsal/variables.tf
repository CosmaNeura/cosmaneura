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

variable "enable_private_executor_network_foundation" {
  description = "Delegate the reserved executor subnet and create the isolated private-service-endpoint subnet after separate plan approval."
  type        = bool
  default     = false
}

variable "private_executor_network_plan_approved" {
  description = "Action-time approval that the saved network-foundation plan is additive, unpeered, private, and limited to the exact 10.76.0.0/24 topology."
  type        = bool
  default     = false
}

variable "enable_private_executor_registry" {
  description = "Create the private Premium registry, its exact private endpoint/DNS path, and job-only AcrPull assignments after separate plan approval."
  type        = bool
  default     = false
}

variable "private_executor_registry_plan_approved" {
  description = "Action-time approval of the exact saved registry plan, global name availability, Premium cost, private topology and job-only pull grants; not runtime activation."
  type        = bool
  default     = false
}

variable "private_executor_registry_name" {
  description = "Globally unique lowercase registry name; revalidate availability before the human-approved apply."
  type        = string
  default     = "cosmaneurarehearsalexec"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{4,49}$", var.private_executor_registry_name))
    error_message = "private_executor_registry_name must be 5-50 lowercase alphanumeric characters starting with a letter"
  }
}

variable "enable_private_executor_vault" {
  description = "Create an empty private RBAC Key Vault and its private endpoint/DNS path; no secret, job read grant or runtime is configured."
  type        = bool
  default     = false
}

variable "private_executor_vault_plan_approved" {
  description = "Action-time approval of the exact saved vault plan, global/soft-deleted name availability, private topology and empty authorization surface."
  type        = bool
  default     = false
}

variable "private_executor_vault_purge_protection_approved" {
  description = "Separate action-time acceptance of irreversible purge protection and 90-day soft-delete retention; vault deletion cannot be purged early."
  type        = bool
  default     = false
}

variable "private_executor_vault_name" {
  description = "Globally unique vault name; verify both active and soft-deleted name availability before apply."
  type        = string
  default     = "cosmaneura-rehearsal-kv"

  validation {
    condition = (
      can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.private_executor_vault_name)) &&
      !strcontains(var.private_executor_vault_name, "--")
    )
    error_message = "private_executor_vault_name must be 3-24 lowercase alphanumeric/hyphen characters, start with a letter, end alphanumeric, and contain no consecutive hyphens"
  }
}

variable "enable_private_evidence_storage" {
  description = "Create the distinct private versioned evidence account and two empty containers; no data, grant, publisher, retention or runtime is configured."
  type        = bool
  default     = false
}

variable "private_evidence_storage_plan_approved" {
  description = "Action-time approval of the exact saved private-evidence plan, account-ID absence/global name availability and private topology."
  type        = bool
  default     = false
}

variable "private_evidence_storage_account_name" {
  description = "Globally unique private-evidence account, distinct from markers; verify availability and exact-ID absence before planning/apply."
  type        = string
  default     = "cosmaneurarehearsalevd"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.private_evidence_storage_account_name))
    error_message = "private_evidence_storage_account_name must be 3-24 lowercase alphanumeric characters"
  }
}

variable "enable_private_launch_mailbox_storage" {
  description = "Create a distinct private versioned mailbox account and two empty action-partitioned containers; no grant, broker, job, retention or runtime is activated."
  type        = bool
  default     = false
}

variable "private_launch_mailbox_storage_plan_approved" {
  description = "Action-time approval of the exact saved mailbox plan, account-ID absence/global availability and private topology."
  type        = bool
  default     = false
}

variable "private_launch_mailbox_storage_account_name" {
  description = "Globally unique mailbox account, distinct from markers and evidence/runtime records; verify availability and exact-ID absence before planning/apply."
  type        = string
  default     = "cosmaneurarehearsalmbx"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.private_launch_mailbox_storage_account_name))
    error_message = "private_launch_mailbox_storage_account_name must be 3-24 lowercase alphanumeric characters"
  }
}

variable "enable_private_restore_broker_identities" {
  description = "Create two unfederated, unattached provision/cleanup broker UAMIs without any role, signing or job-control authority."
  type        = bool
  default     = false
}

variable "private_restore_broker_identity_plan_approved" {
  description = "Action-time approval of the exact saved two-identity plan and authenticated exact-ID absence; not broker activation or authority."
  type        = bool
  default     = false
}

variable "enable_marker_create_assignments" {
  description = "Assign marker-create roles to unfederated job UAMIs only after live provider-fidelity testing proves the exact minimum data actions."
  type        = bool
  default     = false
}

variable "marker_create_authorization_proven" {
  description = "Action-time attestation that live non-PHI tests proved conditional BlockBlob create/read success and overwrite/delete denial for the exact selected add-or-write permission."
  type        = bool
  default     = false
}

variable "private_marker_network_posture_proven" {
  description = "Action-time attestation that readback proved the exact private marker account, Blob private endpoint, private DNS zone, and control-VNet link posture."
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

variable "marker_legal_hold_readback_proven" {
  description = "Action-time attestation that authenticated readback proved the restorecontrolactive legal-hold tag remains active on every retained marker and evidence container."
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
