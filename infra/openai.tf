# https://medium.com/@williamwarley/streamlining-ai-with-azure-a-practical-guide-to-deploying-azure-openai-with-terraform-and-azure-bcbd01bbc3cf
# https://github.com/mucsi96/aks-modules/blob/29f20ea4c56fe192816c2dbd9f4fa641502b7a19/modules/setup_ai/main.tf
# services x region: https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models?tabs=global-standard%2Cstandard-chat-completions#model-summary-table-and-region-availability

resource "azurerm_ai_services" "openai_service" {
  name                = "${local.base_name_alphanumeric}openai"
  resource_group_name = azurerm_resource_group.main.name

  # this seems to be the "primary" region for OpenAI, it has the most recent models
  location = "eastus2"

  sku_name = "S0" # Example SKU, adjust based on actual requirements

  # Required by the AzureOpenAI SDK — without a custom subdomain the regional
  # cognitive endpoint works for curl but the SDK's URL construction
  # (`{endpoint}/openai/deployments/{deployment}/...`) needs a per-resource
  # subdomain. Set manually 2026-05-26 to unblock prod transcription; bringing
  # it into TF state via `tofu import` keeps the value pinned across recreates.
  custom_subdomain_name = "${local.base_name_alphanumeric}openai"
}

resource "azurerm_cognitive_deployment" "openai_4_1" {
  # must match the model name you specify in the OpenAI API call
  name                 = "gpt-4.1-2025-04-14"
  cognitive_account_id = azurerm_ai_services.openai_service.id

  # https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models?tabs=global-standard%2Cstandard-chat-completions#o1-and-o1-mini-models
  model {
    format  = "OpenAI"
    name    = "gpt-4.1"
    version = "2025-04-14"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 50
  }
}

resource "azurerm_cognitive_deployment" "openai_4_1_mini" {
  # must match the model name you specify in the OpenAI API call
  name                 = "gpt-4.1-mini-2025-04-14"
  cognitive_account_id = azurerm_ai_services.openai_service.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 50
  }
}

# restricted based on this form: https://aka.ms/OAI/o1access
resource "azurerm_cognitive_deployment" "openai_o1" {
  name                 = "o1"
  cognitive_account_id = azurerm_ai_services.openai_service.id

  model {
    format  = "OpenAI"
    name    = "o1"
    version = "2024-12-17"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 50
  }
}

# onepassword_item.azure_openai_service removed 2026-05-21 — see note in
# main.tf (Storage Accounts section) on why onepassword_item.* resources
# can't currently be managed from CI.
