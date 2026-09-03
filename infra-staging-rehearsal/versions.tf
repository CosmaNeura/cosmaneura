terraform {
  required_version = "= 1.12.5"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.73.0"
    }

    azapi = {
      source  = "Azure/azapi"
      version = "= 2.1.0"
    }
  }

  # This root intentionally never shares state with infra/ or infra-staging/.
  # Pull-request checks always initialize with -backend=false.
  backend "azurerm" {
    resource_group_name  = "cosmaneura-tfstate"
    storage_account_name = "cosmaneuratfstate"
    container_name       = "cosmaneura-tfstate"
    key                  = "staging-rehearsal-control-plane.tfstate"
  }

}

provider "azurerm" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  # Provider registration is an explicit operator prerequisite. Planning or
  # applying this root must not silently broaden the subscription surface.
  resource_provider_registrations = "none"

  features {}
}

provider "azapi" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}
