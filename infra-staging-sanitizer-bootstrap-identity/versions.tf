terraform {
  required_version = "= 1.12.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.73.0"
    }
  }

  # This root must never share state with the application or rehearsal roots.
  # Pull-request checks always initialize with -backend=false.
  backend "azurerm" {
    resource_group_name  = "cosmaneura-tfstate"
    storage_account_name = "cosmaneuratfstate"
    container_name       = "cosmaneura-tfstate"
    key                  = "staging-sanitizer-bootstrap-identity.tfstate"
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  # Provider registration is an explicit operator prerequisite. This root must
  # not broaden the subscription surface while planning or applying one UAMI.
  resource_provider_registrations = "none"

  features {}
}
