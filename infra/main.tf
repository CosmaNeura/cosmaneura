locals {
  tfstate = {
    storage_account = "cosmaneuratfstate"
    resource_group  = "tfstate-ResourceGroup"
    container       = "tfstate"
    key             = "prod.terraform.tfstate"
  }
}

terraform {
  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
      # azurerm gets updates quite often! Check this page for the latest version:
      # https://github.com/hashicorp/terraform-provider-azurerm/blob/main/CHANGELOG.md
      version = "=4.13.0"
    }

    external = {
      source  = "hashicorp/external"
      version = ">=2.3.3"
    }
  }

  # NOTE this must be hidden initially to bootstrap the account!
  # backend "azurerm" {
  #   resource_group_name = "tfstate-ResourceGroup"
  #   # this must be globally unique across all azure accounts. letters only!
  #   storage_account_name = local.tfstate.storage_account
  #   container_name       = "tfstate"
  #   key                  = "prod.terraform.tfstate"
  # }
}

provider "azurerm" {
  # a subscription (aka account) is a account ID, this is the `id` field on the `az account list`
  subscription_id = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"

  features {
    # skip_provider_registration = true
  }
}

resource "azurerm_resource_group" "main" {
  name = "cosmaneura-prod"
  # this was the cheapest: https://cloudprice.net/regions
  location = "northcentralus"
}

# this should be done automatically when `skip_provider_registration = true`, which is the default, is set
# however, for some reason this just does not work! We have to register this automatically
# resource "azurerm_resource_provider_registration" "containerAppRegistration" {
#   name = "Microsoft.App"
# }

# deployment locking is handled by storing the state of the deployment in a bucket
# IMPORTANT: when bootstrapping a new azure account you must remove the backend block, apply this, then add the backend block again
# data "terraform_remote_state" "tfstate" {
# backend = "azurerm"

# config = {
#   # this is not in the docs, but is really important!
#   # https://github.com/hashicorp/terraform/issues/23515
#   resource_group_name  = local.tfstate.resource_group
#   storage_account_name = local.tfstate.storage_account
#   container_name       = local.tfstate.container
#   key                  = local.tfstate.key
# }
# }

# https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/cache-overview
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/redis_cache
resource "azurerm_redis_cache" "main" {
  # NOTE looks like this name has to be globally unique across all azure accounts
  name                = "${azurerm_resource_group.main.name}-redis"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  # Basic is intended for development, so we use standard
  sku_name = "Standard"
  # small size redis
  capacity = 0
  # non-premium redis
  family = "C"

  non_ssl_port_enabled          = false
  public_network_access_enabled = true
  minimum_tls_version           = 1.2

  # IMPORTANT: make sure docker-compose version aligns with this
  redis_version = 6

  tags = var.tags
}

# https://library.tf/providers/hashicorp/azurerm/latest/docs/resources/s#azurerm_postgresql_flexible_server
# https://azure.microsoft.com/en-us/pricing/details/postgresql/flexible-server/
resource "azurerm_postgresql_flexible_server" "main" {
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  name = "${azurerm_resource_group.main.name}-postgres"

  # the only way to determine pricing and options is go to to azure portal, create a flexible service, and then configure server
  # https://cloudprice.net/vm/Standard_B2ms
  # note that sku name is not the same as what is reported in the azure portal
  # Extracted options from portal:
  # Standard_B1ms (1 vCore, 2 GiB memory, 640 max iops) [~$25]
  # Standard_B2s (2 vCores, 4 GiB memory, 1280 max iops) [~$65]
  # Standard_B2ms (2 vCores, 8 GiB memory, 1920 max iops)
  # Standard_B4ms (4 vCores, 16 GiB memory, 2880 max iops)
  sku_name = "B_Standard_B1ms"

  # 32gb is the min
  storage_mb            = 32768
  auto_grow_enabled     = true
  backup_retention_days = 30

  # for development, we should lock this down later on
  public_network_access_enabled = true

  authentication {
    password_auth_enabled = true
  }

  # TODO I need to pull this from 1p

  # changing this forces a new resource to be created!
  # "Admin username cannot start with numbers and must only contain characters and numbers."
  administrator_login    = data.external.secrets.result["POSTGRES_USERNAME"]
  administrator_password = data.external.secrets.result["POSTGRES_PASSWORD"]

  # in the API docs, only 14 is indicated, but 16 was released
  # https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-supported-versions
  # https://techcommunity.microsoft.com/t5/azure-database-for-postgresql/general-availability-of-postgresql-16-on-azure-database-for/ba-p/3980354#:~:text=PostgreSQL%2016%20introduces%20a%20feature,replication%20directly%20from%20read%20replicas.
  version = "16"

  tags = var.tags
}

# https://support.hashicorp.com/hc/en-us/articles/4548903433235-Terraform-Enterprise-External-Services-mode-with-Azure-Database-for-PostgreSQL-Flexible-Server-Failed-to-Initialize-Plugins
# resource "azurerm_postgresql_flexible_server_configuration" "metabaseExtensions" {
#   name      = "azure.extensions"
#   server_id = azurerm_postgresql_flexible_server.main.id
#   value     = "CITEXT,HSTORE,UUID-OSSP"
# }

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/postgresql_flexible_server_database
resource "azurerm_postgresql_flexible_server_database" "cosmaneura-database" {
  server_id = azurerm_postgresql_flexible_server.main.id

  name      = "production"
  collation = "en_US.utf8"
  charset   = "utf8"

  lifecycle {
    prevent_destroy = true
  }
}

# the following is required in order to expose the azure db to the public internet
# > if you don't specify delegated_subnet_id and private_dns_zone_id the server will be configured with Public access, but you also need to configure firewall rules because by default everything is denied
# https://github.com/hashicorp/terraform-provider-azurerm/issues/14989#issuecomment-1109897547

# resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
#   count = 1

#   name             = "allow-access-from-azure-services"
#   server_id        = azurerm_postgresql_flexible_server.main.id
#   start_ip_address = "0.0.0.0"
#   end_ip_address   = "0.0.0.0"
# }

# resource "azurerm_postgresql_flexible_server_firewall_rule" "all" {
#   count = 1

#   name             = "allow-all-ips"
#   server_id        = azurerm_postgresql_flexible_server.main.id
#   start_ip_address = "0.0.0.0"
#   end_ip_address   = "255.255.255.255"
# }

# ################
# # Metabase
# ################

# resource "azurerm_service_plan" "cosmaneura_plan" {
#   location            = azurerm_resource_group.main.location
#   resource_group_name = azurerm_resource_group.main.name

#   name    = "cosmaneura-app-service-plan"
#   os_type = "Linux"
#   # NOTE that the SKU names are no
#   sku_name = "P1v3"
# }

# # Create an App Service for cosmaneura Scheduler
# resource "azurerm_linux_web_app" "metabase" {
#   location            = azurerm_resource_group.main.location
#   resource_group_name = azurerm_resource_group.main.name

#   # this is a global name since it's attached to a azurewebsites domain
#   name            = "cosmaneura-metabase"
#   service_plan_id = azurerm_service_plan.cosmaneura_plan.id

#   # TODO enable metabase health check
#   # health_check_path = "/api/health"

#   site_config {
#     application_stack {
#       # NOTE if you specify a non-public image at some point on a resource, the image reference may get corrupted
#       #      with an additional `/` in the image name
#       docker_image_name = "metabase/metabase:latest"
#     }

#     # without removing ip restriction you won't be able to access your logs, from the CLI or the web interface! Insane!
#     ip_restriction {
#       ip_address = "0.0.0.0/0"
#       action     = "Allow"
#       priority   = 100
#       name       = "Allow_All_IPs"
#     }
#   }

#   logs {
#     detailed_error_messages = true

#     application_logs {
#       file_system_level = "Verbose"
#     }
#   }


#   app_settings = {
#     # "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"
#     "MB_DB_CONNECTION_URI" = "postgres://${azurerm_postgresql_flexible_server.main.administrator_login}:${azurerm_postgresql_flexible_server.main.administrator_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/metabase"

#     # looks like the default web port on metabase can't be configured
#     WEBSITES_PORT = "3000"
#   }

#   tags = {
#     environment = "production"
#   }
# }

resource "azurerm_log_analytics_workspace" "main" {
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  name = "cosmaneura-scheduler-logs"

  # I literally can't find a description for the SKU options anywhere
  # sku               = "PerGB2018"
  retention_in_days = 30
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/container_app_environment
resource "azurerm_container_app_environment" "main" {
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  name = "${azurerm_resource_group.main.name}-app-environment"

  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  #   kube_environment_id = azurerm_kubernetes_cluster.main.id
}

# TODO why can't we use the external secrets we defined?
locals {
  # NOTE `postgres://` cannot be used with python! It must be `postgresql://`
  postgresql_connection_string = "postgresql://${azurerm_postgresql_flexible_server.main.administrator_login}:${azurerm_postgresql_flexible_server.main.administrator_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/production?sslmode=prefer"
  redis_connection_string      = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:${azurerm_redis_cache.main.ssl_port}?ssl_cert_reqs=optional"
}

resource "azurerm_container_app" "cosmaneura_app_web" {
  resource_group_name = azurerm_resource_group.main.name
  # location            = azurerm_resource_group.main.location

  name                         = "${azurerm_resource_group.main.name}-app-web"
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  template {
    container {
      name = "cosmaneura-web"
      # image = "ghcr.io/cosmaneura/cosmaneura:latest"
      image = "crccheck/hello-world:latest"

      # args = [
      #   "celery",
      #   "-A", "tasks", "beat", "--loglevel=INFO"
      # ]
      # args = [
      #   "celery -A tasks beat --loglevel=INFO"
      # ]

      # args = ["-A", "tasks", "beat", "--loglevel=INFO"]

      # in order to trigger a release each time you apply the terraform
      # without this, the latest version of a tag will not be deployed :/
      # env {
      #   name  = "RELEASE_TIMESTAMP"
      #   value = timestamp()
      # }

      env {
        name  = "DATABASE_URL"
        value = local.postgresql_connection_string
      }

      env {
        name  = "REDIS_URL"
        value = local.redis_connection_string
      }

      # env {
      #   name  = "IPINFO_API_KEY"
      #   value = data.external.IPINFO_API_KEY.result["password"]
      # }

      # yes, memory is defined as a string and CPU is defined as a float
      cpu    = 0.5
      memory = "1Gi"
    }
  }

  # registry {
  #   # unlike other registry configs, this does NOT contain a protocol
  #   server   = "ghcr.io"
  #   username = "iloveitaly"
  #   # why do I need to register a secret separately?
  #   password_secret_name = "registry-secret"
  # }

  # secret {
  #   name  = "registry-secret"
  #   value = var.GH_GHCR_TOKEN
  # }

  tags = var.tags
}

# resource "azurerm_container_app" "cosmaneura_worker" {
#   resource_group_name          = azurerm_resource_group.main.name
#   container_app_environment_id = azurerm_container_app_environment.main.id

#   name          = "cosmaneura-worker"
#   revision_mode = "Single"

#   template {
#     container {
#       name  = "cosmaneura-worker"
#       image = "ghcr.io/cosmaneura/cosmaneura:latest"

#       # in order to trigger a release each time you apply the terraform
#       # without this, the latest version of a tag will not be deployed :/
#       env {
#         name  = "RELEASE_TIMESTAMP"
#         value = timestamp()
#       }

#       env {
#         name  = "CELERY_WORKER_CONCURRENCY"
#         value = "1"
#       }

#       # TODO do I really need to duplicate all of these? insane!

#       env {
#         name  = "DATABASE_URL"
#         value = local.postgresql_connection_string
#       }

#       env {
#         name  = "LOG_LEVEL"
#         value = "INFO"
#       }

#       env {
#         name  = "REDIS_URL"
#         value = local.redis_connection_string
#       }

#       env {
#         name  = "OPENAI_API_KEY"
#         value = data.external.OPENAI_API_KEY.result["password"]
#       }

#       # yes, memory is defined as a string and CPU is defined as a float
#       cpu    = 2
#       memory = "4Gi"
#     }
#   }

#   registry {
#     # unlike other registry configs, this does NOT contain a protocol
#     server   = "ghcr.io"
#     username = "iloveitaly"
#     # why do I need to register a secret separately?
#     password_secret_name = "registry-secret"
#   }

#   secret {
#     name  = "registry-secret"
#     value = var.GH_GHCR_TOKEN
#   }

#   tags = var.tags
# }
