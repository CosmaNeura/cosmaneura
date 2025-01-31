# https://medium.com/@williamwarley/streamlining-ai-with-azure-a-practical-guide-to-deploying-azure-openai-with-terraform-and-azure-bcbd01bbc3cf
# https://github.com/mucsi96/aks-modules/blob/29f20ea4c56fe192816c2dbd9f4fa641502b7a19/modules/setup_ai/main.tf

resource "azurerm_ai_services" "openai_service" {
  name                = "${local.base_name_alphanumeric}openai"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  sku_name = "S0"  # Example SKU, adjust based on actual requirements
}

resource "azurerm_cognitive_deployment" "openai_deployment" {
  name                 = "${azurerm_ai_services.openai_service.name}-gpt-4o"
  cognitive_account_id = azurerm_ai_services.openai_service.id

  # https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models?tabs=global-standard%2Cstandard-chat-completions#o1-and-o1-mini-models
  model {
    format  = "OpenAI"
    name    = "gpt-4o"
    version = "2024-11-20"
  }

  sku {
    name     = "GlobalStandard"
    # capacity = 5
  }
}

# restricted based on this form: https://aka.ms/OAI/o1access
# resource "azurerm_cognitive_deployment" "openai_o1" {
#   name                 = "${azurerm_ai_services.openai_service.name}-gpt-o1"
#   cognitive_account_id = azurerm_ai_services.openai_service.id

#   model {
#     format  = "OpenAI"
#     name    = "o1"
#     # version = "2024-08-06"
#   }

#   sku {
#     name     = "GlobalStandard"
#     # capacity = 5
#   }
# }
