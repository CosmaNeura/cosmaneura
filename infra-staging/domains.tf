####################
# SSL Generation
#
# https://github.com/xaviermignot/terraform-certificates/blob/main/main.tf
# https://github.com/hashicorp/terraform-provider-azurerm/issues/21866#issuecomment-2455147510
####################

# the flow here is  wonky:
#
#   - this operation will fail unless the correct record exists on the target name/host
#   - you cannot know what is required until you run this command
#   - therefore, apply this TF module, look at the error, update the DNS, and then rerun. You'll get an error containing
#     something like 'A TXT record pointing from ...' which you can use to update the DNS.
#   - after rerunning the domain will be successfully verified, but this module will async attempt to bind to the
#     specified domain. This will fail if the CNAME of the targeted domain is not set to the fqdn of the container app
#   - if you get into a failed state, you'll need to manually delete the domain.
#   - although the app environment is not explicitly tied to the resources here, the certificate is generated through
#     the application environment.
#
# https://learn.microsoft.com/en-us/azure/security/fundamentals/subdomain-takeover
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app_custom_domain
resource "azurerm_container_app_custom_domain" "main" {
  # name             = trimsuffix(trimprefix(azurerm_dns_txt_record.api.fqdn, "asuid."), ".")
  # name is the hostname you want to generate a certificate for
  name             = "app.cosmaneura.com"
  container_app_id = azurerm_container_app.app.id

  certificate_binding_type = "SniEnabled"

  lifecycle {
    // When using an Azure created Managed Certificate these values must be added to ignore_changes to prevent resource recreation.
    ignore_changes = [certificate_binding_type, container_app_environment_certificate_id]
  }
}

# generates a certificate for the custom domain in the container app environment
resource "azapi_resource" "managed_certificate" {
  # depends_on = [azurerm_dns_cname_record.api]
  type      = "Microsoft.App/managedEnvironments/managedCertificates@2024-03-01"
  name      = local.name
  parent_id = azurerm_container_app_environment.main.id
  location  = azurerm_resource_group.main.location

  body = {
    properties = {
      subjectName             = azurerm_container_app_custom_domain.main.name
      domainControlValidation = "CNAME"
    }
  }

  response_export_values = ["*"]

  tags = var.tags
}

# within the container app, this generates the "binding" between the custom domain and the container
resource "azapi_resource_action" "apply_custom_domain_binding" {
  resource_id = azurerm_container_app.app.id
  when        = "apply"
  type        = "Microsoft.App/containerApps@2024-03-01"
  method      = "PATCH"
  body = {
    properties = {
      configuration = {
        ingress = {
          customDomains = [
            # if a container is bound to multiple domains, you have to list them all out in a single call here
            {
              bindingType   = "SniEnabled",
              name          = azurerm_container_app_custom_domain.main.name,
              certificateId = azapi_resource.managed_certificate.output.id
            },
            {
              bindingType   = "SniEnabled",
              name          = azurerm_container_app_custom_domain.chat.name,
              certificateId = azapi_resource.chat_managed_certificate.output.id
            }
          ]
        }
      }
    }
  }
}

resource "azapi_resource_action" "destroy_custom_domain_binding" {
  depends_on  = [azurerm_container_app_custom_domain.main]
  when        = "destroy"
  resource_id = azurerm_container_app.app.id
  #On destroy provisioner necessary to remove domain before removing Certificate see error in comments below
  type   = "Microsoft.App/containerApps@2024-03-01"
  method = "PATCH"
  body = {
    properties = {
      configuration = {
        ingress = {
          customDomains = []
        }
      }
    }
  }
}

####################
# Chat Domain
####################

resource "azurerm_container_app_custom_domain" "chat" {
  name             = "chat.cosmaneura.com"
  container_app_id = azurerm_container_app.app.id

  certificate_binding_type = "SniEnabled"

  lifecycle {
    // When using an Azure created Managed Certificate these values must be added to ignore_changes to prevent resource recreation.
    ignore_changes = [certificate_binding_type, container_app_environment_certificate_id]
  }
}

resource "azapi_resource" "chat_managed_certificate" {
  # depends_on = [azurerm_dns_cname_record.api]
  type = "Microsoft.App/managedEnvironments/managedCertificates@2024-03-01"
  name = "${local.name}-chat"

  parent_id = azurerm_container_app_environment.main.id
  location  = azurerm_resource_group.main.location

  body = {
    properties = {
      subjectName             = azurerm_container_app_custom_domain.chat.name
      domainControlValidation = "CNAME"
    }
  }

  response_export_values = ["*"]

  tags = var.tags
}

# within the container app, this generates the "binding" between the custom domain and the container
# resource "azapi_resource_action" "chat_apply_custom_domain_binding" {
#   resource_id = azurerm_container_app.app.id
#   when        = "apply"
#   type        = "Microsoft.App/containerApps@2024-03-01"
#   method      = "PATCH"
#   body = {
#     properties = {
#       configuration = {
#         ingress = {
#           customDomains = [
#             {
#               bindingType   = "SniEnabled",
#               name          = azurerm_container_app_custom_domain.chat.name,
#               certificateId = azapi_resource.chat_managed_certificate.output.id
#             },
#             {
#               bindingType   = "SniEnabled",
#               name          = azurerm_container_app_custom_domain.main.name,
#               certificateId = azapi_resource.chat_managed_certificate.output.id
#             }
#           ]
#         }
#       }
#     }
#   }
# }

resource "azapi_resource_action" "chat_destroy_custom_domain_binding" {
  depends_on  = [azurerm_container_app_custom_domain.chat]
  when        = "destroy"
  resource_id = azurerm_container_app.app.id
  #On destroy provisioner necessary to remove domain before removing Certificate see error in comments below
  type   = "Microsoft.App/containerApps@2024-03-01"
  method = "PATCH"
  body = {
    properties = {
      configuration = {
        ingress = {
          customDomains = []
        }
      }
    }
  }
}
