# tips on debugging issues:
#
#   - https://github.com/hashicorp/terraform-provider-azurerm is the best place to search for issues
#   - https://github.com/hashicorp/terraform-provider-azurerm/blob/main/CHANGELOG.md

locals {
  # configuration to store tf state for ability for multiple devs to deploy
  tfstate = {
    # this must be globally unique across all azure accounts. letters only!
    storage_account = "cosmaneuratfstate"
    # resource group should be distinct from the resource group of the deployment
    resource_group = "cosmaneura-tfstate"
    container      = "cosmaneura-tfstate"
    key            = "${local.environment}.tfstate"
  }

  azure_subscription_id = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
  azure_tenant_id       = "d51754d0-bba5-4810-820e-afac542844d6"
  environment           = "prod"
  name                  = "cosmaneura-${local.environment}"

  base_name = azurerm_resource_group.main.name
  # azure requires alpha numeric chars only in many places
  base_name_alphanumeric = replace(local.base_name, "/[^a-zA-Z0-9]/", "")
  deid_service_name      = "AnonymizerAPI"
  deid_service_resource_id = (
    "${azurerm_resource_group.main.id}/providers/Microsoft.HealthDataAIServices/deidServices/${local.deid_service_name}"
  )
  deid_managed_identity_live_guard_valid = (
    !var.enable_deid_managed_identity ||
    try(data.external.deid_managed_identity_live_guard.result["result"] == "verified", false)
  )

  # DATABASE_URL rollover is a two-phase, source-controlled operation.  Both
  # values can be staged while every workload still references database-url;
  # database_url_cutover.py then moves workloads one at a time.  Updating the
  # non-secret selector after that staged move makes the choice durable across
  # future OpenTofu applies.  The old secret remains declared for rollback.
  database_url_v2_enabled_text = try(data.external.secrets.result["DATABASE_URL_V2_ENABLED"], "false")
  database_url_v2_enabled      = local.database_url_v2_enabled_text == "true"
  database_url_secret_name     = try(data.external.secrets.result["DATABASE_URL_SECRET_NAME"], "")
  database_url_v2_value        = try(data.external.secrets.result["DATABASE_URL_V2"], "")
  database_url_v2_secret_values = local.database_url_v2_enabled ? {
    "database-url-v2" = local.database_url_v2_value
  } : {}
  database_url_configuration_valid = (
    contains(["true", "false"], local.database_url_v2_enabled_text) &&
    contains(["database-url", "database-url-v2"], local.database_url_secret_name) &&
    trimspace(data.external.secrets.result["DATABASE_URL"]) != "" &&
    (!local.database_url_v2_enabled || trimspace(local.database_url_v2_value) != "") &&
    (local.database_url_secret_name != "database-url-v2" || local.database_url_v2_enabled)
  )
  database_url_live_guard_valid = try(
    data.external.database_url_live_guard.result["result"] == "verified",
    false,
  )

  stripe_integration_enabled_text = lower(try(data.external.secrets.result["STRIPE_INTEGRATION_ENABLED"], "false"))
  stripe_billing_enabled_text     = lower(try(data.external.secrets.result["STRIPE_BILLING_ENABLED"], "false"))
  stripe_integration_enabled      = local.stripe_integration_enabled_text == "true"
  stripe_billing_enabled          = local.stripe_billing_enabled_text == "true"
  stripe_configuration_valid = (
    contains(["true", "false"], local.stripe_integration_enabled_text) &&
    contains(["true", "false"], local.stripe_billing_enabled_text) &&
    lower(try(data.external.secrets.result["STRIPE_LIVE_MODE"], "")) == "true" &&
    (!local.stripe_billing_enabled || local.stripe_integration_enabled) &&
    (!local.stripe_integration_enabled || (
      (
        startswith(trimspace(try(data.external.secrets.result["STRIPE_SECRET_KEY"], "")), "sk_live_") ||
        startswith(trimspace(try(data.external.secrets.result["STRIPE_SECRET_KEY"], "")), "rk_live_")
      ) &&
      startswith(trimspace(try(data.external.secrets.result["STRIPE_WEBHOOK_SECRET"], "")), "whsec_") &&
      startswith(trimspace(try(data.external.secrets.result["STRIPE_ACCOUNT_ID"], "")), "acct_") &&
      startswith(trimspace(try(data.external.secrets.result["STRIPE_PRICE_ID"], "")), "price_") &&
      startswith(trimspace(try(data.external.secrets.result["STRIPE_PORTAL_CONFIGURATION_ID"], "")), "bpc_") &&
      trimspace(try(data.external.secrets.result["STRIPE_API_VERSION"], "")) != ""
    ))
  )

  recall_webhook_secret         = try(data.external.secrets.result["RECALL_WEBHOOK_SECRET"], "")
  recall_webhook_secret_payload = trimprefix(local.recall_webhook_secret, "whsec_")
  # Validate only the Recall/Svix whsec_ + canonical base64 shape. Never place
  # the credential in an output, diagnostic, summary, or provider query.
  recall_webhook_secret_valid = (
    local.recall_webhook_secret == trimspace(local.recall_webhook_secret) &&
    startswith(local.recall_webhook_secret, "whsec_") &&
    # Any canonical base64 encoding of at least 16 decoded bytes is 24 or more
    # characters. This rejects syntactically valid but trivially weak keys.
    length(local.recall_webhook_secret_payload) >= 24 &&
    length(local.recall_webhook_secret_payload) % 4 == 0 &&
    can(regex(
      "^([A-Za-z0-9+/]{4})*([A-Za-z0-9+/][AQgw]==|[A-Za-z0-9+/]{2}[AEIMQUYcgkosw048]=)?$",
      local.recall_webhook_secret_payload,
    ))
  )
}

terraform {
  required_providers {
    # when changing provider versions, you must run `tofu init`
    azurerm = {
      source = "hashicorp/azurerm"
      # azurerm gets updates quite often! Check this page for the latest version:
      # https://github.com/hashicorp/terraform-provider-azurerm/blob/main/CHANGELOG.md
      version = ">=4.14.0"
    }

    # required to work around azure provider limitations
    azapi = {
      source  = "Azure/azapi"
      version = "2.1.0"
    }

    # to sync credentials to 1password that are generated by azure setup
    onepassword = {
      source  = "1password/onepassword"
      version = ">=2.1.2"
    }

    # extract secrets from 1password via direnv
    external = {
      source  = "hashicorp/external"
      version = ">=2.3.4"
    }

    # Transitional state dependency: the removed Flower password remains in
    # production state until the approved removal plan is applied. Keep this
    # provider and its lock entry for that destroy, then remove both afterward.
    random = {
      source  = "hashicorp/random"
      version = ">=3.6.0"
    }
  }

  # NOTE this must be hidden initially to bootstrap the account!
  backend "azurerm" {
    # Variables here cannot reference created resources and must only reference local variables.
    resource_group_name = local.tfstate.resource_group
    # this must be globally unique across all azure accounts. letters only!
    storage_account_name = local.tfstate.storage_account
    container_name       = local.tfstate.container
    key                  = local.tfstate.key
  }
}

provider "onepassword" {
  # without this the OP_ACCOUNT password will be picked up and conflict with the OP_SERVICE_ACCOUNT_TOKEN, which is not what we want
  account = ""
}

provider "azurerm" {
  # a subscription (aka account) is a account ID, this is the `id` field on the `az account list`
  subscription_id = local.azure_subscription_id

  features {
    # skip_provider_registration = true
  }
}

# resource group where all service-related resources are stored
resource "azurerm_resource_group" "main" {
  # TOOD should the name contain the environment definition?
  name = "cosmaneura-prod"
  # this seemed to be the cheapest: https://cloudprice.net/regions
  # my analysis or understanding of pricing is limited, so this may be incorrect
  location = "northcentralus"
  tags     = var.tags
}

# TODO this could have been fixed in a recent version of the azure provider, consider removing.
# this should be done automatically when `skip_provider_registration = true`, which is the default, is set
# however, for some reason this just does not work! We have to register this automatically
# resource "azurerm_resource_provider_registration" "containerAppRegistration" {
#   name = "Microsoft.App"
# }

########################################
# Deployment Resource State
########################################

# resource group to hold common terraform state for deployment
# IMPORTANT: (1) when bootstrapping a new azure account, this must be done first!
resource "azurerm_resource_group" "tfstate" {
  name     = local.tfstate.resource_group
  location = "northcentralus"

  # TODO customize tags since this is distinct from the deployment resource group
  tags = var.tags
}

# IMPORTANT: (2) when bootstrapping a new azure account, this must be done second!
resource "azurerm_storage_account" "tfstate" {
  name                = local.tfstate.storage_account
  resource_group_name = azurerm_resource_group.tfstate.name
  location            = azurerm_resource_group.tfstate.location

  account_tier = "Standard"
  # locally redundant storage is the cheapest
  account_replication_type = "LRS"

  # State must never be anonymously readable, even while the backend still
  # uses its separately controlled shared-key compatibility path.
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"

  tags = var.tags
}

resource "azurerm_storage_container" "tfstate" {
  name                  = local.tfstate.container
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}

# deployment locking is handled by storing the state of the deployment in a bucket
# IMPORTANT: now, with a resource group and storage account in place
data "terraform_remote_state" "tfstate" {
  backend = "azurerm"

  config = {
    # this is not in the docs, but is really important!
    # https://github.com/hashicorp/terraform/issues/23515
    resource_group_name  = azurerm_resource_group.tfstate.name
    storage_account_name = azurerm_storage_account.tfstate.name
    container_name       = azurerm_storage_container.tfstate.name
    key                  = local.tfstate.key
  }
}

########################################
# Upload Storage
########################################

# used for audio uploads
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_account#attributes-reference
resource "azurerm_storage_account" "uploads" {
  name                = "${local.base_name_alphanumeric}uploads"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  account_tier = "Standard"
  # locally redundant storage is the cheapest
  account_replication_type = "LRS"

  # Account-level public blob creation is safe to disable independently of the
  # later managed-identity/private-network cutovers. Shared-key and public
  # network access remain explicit compatibility blockers until those rungs
  # have synthetic evidence and a separately approved plan.
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = true
  shared_access_key_enabled       = true

  blob_properties {
    #
    cors_rule {
      max_age_in_seconds = 86400
      # TODO we should explicitly limit origins, but leaving wide open for development
      allowed_origins = ["*"]
      allowed_headers = ["*"]
      allowed_methods = ["PUT"]
      exposed_headers = ["*"]
    }
  }

  tags = var.tags
}

# automatically delete audio files after 10d
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_management_policy
resource "azurerm_storage_management_policy" "delete_uploads" {
  storage_account_id = azurerm_storage_account.uploads.id

  rule {
    name    = "delete-files"
    enabled = true

    filters {
      blob_types = ["blockBlob"]
      # by default, all storage containers have the same rule applied, so we need to explicitly filter on storage container name
      prefix_match = [
        azurerm_storage_container.uploads.name,
        azurerm_storage_container.example_notes.name,
        # NOTE test-* is explicitly not deleted!
      ]
    }

    # IMPORTANT: this ensures files are wiped automatically after uploaded
    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = 10
      }

      snapshot {
        delete_after_days_since_creation_greater_than = 10
      }
    }
  }
}

# like a bucket or folder within the storage account
resource "azurerm_storage_container" "uploads" {
  name                  = "uploads"
  storage_account_id    = azurerm_storage_account.uploads.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "example_notes" {
  name                  = "example-notes"
  storage_account_id    = azurerm_storage_account.uploads.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "test_uploads" {
  name                  = "test-uploads"
  storage_account_id    = azurerm_storage_account.uploads.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "test_example_notes" {
  name                  = "test-example-notes"
  storage_account_id    = azurerm_storage_account.uploads.id
  container_access_type = "private"
}

# NOTE: onepassword_item.upload_blob_storage and other onepassword_item.*
# resources were removed on 2026-05-21 because the CI service account
# lacks write access to OP_PROD_VAULT (every `tofu apply` from CI failed
# trying to create them — see PR 42 incident). The actual 1Password
# entries continue to exist; they were created manually outside TF and
# are read at runtime via the `data "external" "secrets"` direnv chain.
# When the SA gets vault-write permission, these can move back into TF
# (`terraform import` against the existing item IDs, not create-from-
# scratch which would duplicate them).

####################
# Container Registry
####################

# the ghcr.io container registry is quite expensive:
#  - our containers are ~1gb a piece
#  - ghcr charges for data transfer, $0.25/gb, so we would pay $0.25 per deploy, which feels insane
#  - ghcr seems poorly maintained
#  - the auth story is poor on ghcr, seems like you have to use a PAT, which feels brittle
#
# for this reason, we are going to use an azure container registry instead.
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_registry
resource "azurerm_container_registry" "main" {
  # only alphanumeric is allowed
  name                = "${local.base_name_alphanumeric}registry"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  tags                = var.tags

  # in Basic, you do not have access to advanced registry tokens
  admin_enabled = true

  # only supported in premium
  # data_endpoint_enabled = true
}

# onepassword_item.azure_container_registry removed 2026-05-21 — see
# note above onepassword_item.upload_blob_storage at the top of the
# Storage Accounts section.

####################
# Redis
####################

# https://learn.microsoft.com/en-us/azure/azure-cache-for-redis/cache-overview
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/redis_cache
resource "azurerm_redis_cache" "main" {
  # NOTE looks like this name has to be globally unique across all azure accounts
  name                = "${local.base_name}-redis"
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

# onepassword_item.production_redis removed 2026-05-21 — see note above.

####################
# PostgreSQL
####################

# https://library.tf/providers/hashicorp/azurerm/latest/docs/resources/s#azurerm_postgresql_flexible_server
# https://azure.microsoft.com/en-us/pricing/details/postgresql/flexible-server/
resource "azurerm_postgresql_flexible_server" "main" {
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  name = "${local.base_name}-postgres"

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

  # TODO for development, we should lock this down later on
  public_network_access_enabled = true

  authentication {
    active_directory_auth_enabled = var.enable_scribe_database_entra_auth
    password_auth_enabled         = true
    tenant_id = (
      var.enable_scribe_database_entra_auth
      ? local.azure_tenant_id
      : null
    )
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

locals {
  # NOTE `postgres://` cannot be used with python! It must be `postgresql://`
  postgresql_connection_string = "postgresql://${azurerm_postgresql_flexible_server.main.administrator_login}:${azurerm_postgresql_flexible_server.main.administrator_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/production?sslmode=verify-full&sslrootcert=%2Fetc%2Fssl%2Fcerts%2Fca-certificates.crt"
  redis_connection_string      = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:${azurerm_redis_cache.main.ssl_port}?ssl_cert_reqs=optional"
}

# add postgres connection string to 1password
# https://registry.terraform.io/providers/1Password/onepassword/latest/docs
# onepassword_item.production_postgres removed 2026-05-21 — see note above.

####################
# Public DB Access
#
# The following is required in order to expose the azure db to the public internet:
# > if you don't specify delegated_subnet_id and private_dns_zone_id the server will be configured with Public access\
# but you also need to configure firewall rules because by default everything is denied
# https://github.com/hashicorp/terraform-provider-azurerm/issues/14989#issuecomment-1109897547
####################

resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "allow-access-from-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "aca_prod_egress" {
  name             = "aca-prod-egress"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = var.production_container_apps_egress_ipv4
  end_ip_address   = var.production_container_apps_egress_ipv4

  lifecycle {
    precondition {
      condition = try(
        one(azurerm_container_app.scheduler.outbound_ip_addresses) == var.production_container_apps_egress_ipv4,
        false,
      )
      error_message = "production_container_apps_egress_ipv4 does not exactly match the single refreshed scheduler outbound address; stop and reconcile live routing before continuing"
    }
  }
}

# The exact rule is created imperatively before the broad production rule is
# removed. Adopt it during the next reviewed production plan/apply instead of
# attempting a duplicate create.
import {
  for_each = var.adopt_existing_production_postgres_egress_rule ? toset(["aca-prod-egress"]) : toset([])
  to       = azurerm_postgresql_flexible_server_firewall_rule.aca_prod_egress
  id       = "${azurerm_postgresql_flexible_server.main.id}/firewallRules/${each.value}"
}

####################
# Container Registry
####################

resource "azurerm_log_analytics_workspace" "main" {
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  name = "${local.base_name}-logs"

  # I literally can't find a description for the SKU options anywhere
  # sku               = "PerGB2018"
  retention_in_days = 30

  tags = var.tags
}

# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/container_app_environment
resource "azurerm_container_app_environment" "main" {
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  name = "${local.base_name}-app-environment"

  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  #   kube_environment_id = azurerm_kubernetes_cluster.main.id

  # `custom_domain_verification_id` represents the TXT record that must be added to a `asuid.` record
  # this value must be manually set within squarespace. Take a look at the readme for more information.

  tags = var.tags
}

####################
# Application Container
####################

# this module is very temperamental: if you fail to deploy it, it can get in a corrupt state that terraform can't get it out
# of. The easiest escape hatch here is a full deletion.
#
# https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app
# https://github.com/microsoft/NubesGen/blob/0d5d4896f5f2c93d1baca01807527bee56ff49b8/terraform/modules/container-apps/main.tf#L91
resource "azurerm_container_app" "app" {
  resource_group_name = azurerm_resource_group.main.name
  # NOTE location is determined automatically by the resource group

  name                         = "${azurerm_resource_group.main.name}-app"
  container_app_environment_id = azurerm_container_app_environment.main.id

  # https://learn.microsoft.com/en-us/azure/container-apps/application-lifecycle-management
  # > When in single revision mode, Container Apps handles the automatic switch between revisions to support zero downtime deployment.
  revision_mode = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.scribe_workflow.id]
  }

  ingress {
    external_enabled = true
    target_port      = 80

    traffic_weight {
      percentage = 100
      # "If a traffic splitting rule is configured with latestRevision set to true, traffic doesn't switch to the latest revision until it's ready"
      latest_revision = true
    }
  }

  template {
    # without this, if the container fails, it will not be restarted even if tf config is updated
    min_replicas = 1

    container {
      name = "${local.base_name}-app-web"

      # great starter image to just get something running
      # https://github.com/crccheck/docker-hello-world/blob/master/Dockerfile
      # image = "crccheck/hello-world:latest"

      # the amount of time I wasted realizing that private images must be fully qualified, but public images do not need to be is embarrassing
      # https://github.com/hashicorp/terraform-provider-azurerm/issues/22459
      image = "${azurerm_container_registry.main.login_server}/cosmaneura/cosmaneura:latest"

      # the docs state that these probes are automatically setup with the ingress port, so it's unclear why they need
      # to be defined here. Additionally, the defaults used by terraform for these probes are not the same as the azure
      # documentation.args =
      readiness_probe {
        port      = 80
        transport = "TCP"
      }

      startup_probe {
        port      = 80
        transport = "TCP"
      }

      liveness_probe {
        port      = 80
        transport = "TCP"
      }

      # https://learn.microsoft.com/en-us/azure/container-apps/health-probes?tabs=arm-template

      args = [data.external.procfile.result["api"]]

      dynamic "env" {
        for_each = setunion(var.python_public_vars, var.python_billing_public_vars)

        content {
          name  = env.key
          value = data.external.secrets.result[env.key]
        }
      }

      dynamic "env" {
        for_each = setunion(
          var.python_secret_vars,
          var.python_api_only_secret_vars,
          local.stripe_integration_enabled ? var.python_billing_secret_vars : toset([]),
          local.stripe_integration_enabled ? var.python_billing_api_only_secret_vars : toset([]),
        )

        content {
          name        = env.key
          secret_name = env.key == "DATABASE_URL" ? local.database_url_secret_name : replace(lower(env.key), "_", "-")
        }
      }

      dynamic "env" {
        for_each = local.core_cab_public_values
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.scribe_native_api_public_values
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = merge(
          local.session_prep_shared_public_values,
          local.session_prep_authority_runtime_public_values,
          local.session_prep_api_public_values,
        )
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.core_cab_secret_values
        content {
          name        = env.key
          secret_name = replace(lower(env.key), "_", "-")
        }
      }

      env {
        name  = "SCRIBE_WORKFLOW_ENABLED"
        value = tostring(var.enable_scribe_workflow)
      }

      env {
        name  = "SCRIBE_MANAGED_IDENTITY_CLIENT_ID"
        value = azurerm_user_assigned_identity.scribe_workflow.client_id
      }

      dynamic "env" {
        for_each = var.enable_scribe_workflow ? [1] : []
        content {
          name  = "SCRIBE_SERVICE_BASE_URL"
          value = var.scribe_service_base_url
        }
      }

      dynamic "env" {
        for_each = var.enable_scribe_workflow ? [1] : []
        content {
          name  = "SCRIBE_SERVICE_SCOPE"
          value = var.scribe_service_scope
        }
      }

      env {
        name  = "SCRIBE_SERVICE_TIMEOUT_SECONDS"
        value = tostring(var.scribe_service_timeout_seconds)
      }

      # yes, memory is defined as a string and CPU is defined as a float
      cpu    = 0.5
      memory = "1Gi"
    }
  }

  dynamic "secret" {
    for_each = setunion(
      var.python_secret_vars,
      var.python_api_only_secret_vars,
      local.stripe_integration_enabled ? var.python_billing_secret_vars : toset([]),
      local.stripe_integration_enabled ? var.python_billing_api_only_secret_vars : toset([]),
    )

    content {
      name  = replace(lower(secret.key), "_", "-")
      value = data.external.secrets.result[secret.key]
    }
  }

  dynamic "secret" {
    for_each = local.core_cab_secret_values
    content {
      name  = replace(lower(secret.key), "_", "-")
      value = secret.value
    }
  }

  dynamic "secret" {
    for_each = local.database_url_v2_secret_values

    content {
      name  = secret.key
      value = secret.value
    }
  }

  # TODO could use identity based login in the future, but the Basic tier of acr doesn't support this
  # https://github.com/microsoft/NubesGen/blob/0d5d4896f5f2c93d1baca01807527bee56ff49b8/terraform/modules/container-apps/main.tf#L91
  registry {
    # unlike other registry configs, this does NOT contain a protocol
    server   = azurerm_container_registry.main.login_server
    username = azurerm_container_registry.main.admin_username

    # why is the password stored in a separate secret block? No idea. But, This is The Way.
    password_secret_name = "registry-secret"
  }

  secret {
    name  = "registry-secret"
    value = azurerm_container_registry.main.admin_password
  }

  # build_and_publish_manual.yml deploys SHA-pinned images via
  # `az containerapp update --set-image SHA`; without this block tofu
  # apply reverts the live image back to ":latest", which silently rolls
  # the running revision to whatever ":latest" points to. Image lifecycle
  # is owned by the deploy pipeline, not Terraform.
  lifecycle {
    precondition {
      condition     = length(local.scribe_native_api_env_name_collisions) == 0
      error_message = "Native Scribe and durable Zoom rollout settings cannot also be supplied through generic API environment-name sets"
    }
    precondition {
      condition     = local.scribe_native_workload_prerequisites_valid
      error_message = "native workload activation requires the existing canonical Clerk issuer"
    }

    precondition {
      condition     = length(local.session_prep_api_env_name_collisions) == 0
      error_message = "Session Prep rollout settings cannot also be supplied through generic API environment-name sets"
    }

    precondition {
      condition     = local.session_prep_production_prerequisites_valid
      error_message = "Session Prep production activation requires the exact Clerk issuer and an existing record-grant signing secret"
    }

    precondition {
      condition     = local.core_cab_configuration_valid
      error_message = "Core CAB runtime flags and dedicated workload configuration are inconsistent"
    }

    precondition {
      condition     = local.recall_webhook_secret_valid
      error_message = "RECALL_WEBHOOK_SECRET must use a whsec_ prefix and canonical base64 payload of at least 16 decoded bytes"
    }

    precondition {
      condition     = local.database_url_configuration_valid
      error_message = "DATABASE_URL rollover configuration must select one configured database-url secret"
    }

    precondition {
      condition     = local.database_url_live_guard_valid
      error_message = "live DATABASE_URL state must already match the source-controlled selector"
    }

    precondition {
      condition     = local.stripe_configuration_valid
      error_message = "Stripe integration/acquisition flags and required configuration are inconsistent"
    }

    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  tags = var.tags
}

# Celery beat runs in its own container so a hung scheduler can't bring
# down task execution and a worker crash doesn't pause the daily schedule.
# REQUIRES: deploy a build that has --beat removed from Procfile's worker
# entry BEFORE applying this — otherwise both this scheduler and the old
# worker fight for the redbeat lock and one of them dies every ~25 minutes.
resource "azurerm_container_app" "scheduler" {
  resource_group_name = azurerm_resource_group.main.name

  name                         = "${azurerm_resource_group.main.name}-scheduler"
  container_app_environment_id = azurerm_container_app_environment.main.id

  revision_mode = "Single"

  template {
    # min == max == 1 is required: running two celery beat processes would
    # double-fire every scheduled task. RedBeat's lock prevents that in
    # practice but the safety net should be at the infra level, not relying
    # on Redis-level coordination.
    min_replicas = 1
    max_replicas = 1

    custom_scale_rule {
      name             = "scheduler-cpu"
      custom_rule_type = "cpu"
      metadata = {
        type  = "Utilization"
        value = "80"
      }
    }

    container {
      name = "${local.base_name}-app-scheduler"

      # `celery beat` does not open any port; HTTP probes would always fail.
      # TCP on a closed port is also useless. Skip probes entirely and rely
      # on container exit code: if beat raises uncaught, the process exits
      # and Container Apps restarts it.

      image = "${azurerm_container_registry.main.login_server}/cosmaneura/cosmaneura:latest"

      args = [data.external.procfile.result["scheduler"]]

      dynamic "env" {
        for_each = var.python_public_vars

        content {
          name  = env.key
          value = data.external.secrets.result[env.key]
        }
      }

      dynamic "env" {
        for_each = var.python_secret_vars

        content {
          name        = env.key
          secret_name = env.key == "DATABASE_URL" ? local.database_url_secret_name : replace(lower(env.key), "_", "-")
        }
      }

      dynamic "env" {
        for_each = local.zoom_durable_public_values

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.session_prep_shared_public_values

        content {
          name  = env.key
          value = env.value
        }
      }

      # Beat is lightweight: it polls Redis every few seconds and enqueues
      # the occasional task. Half a CPU and 512Mi is generous.
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }

  dynamic "secret" {
    for_each = var.python_secret_vars

    content {
      name  = replace(lower(secret.key), "_", "-")
      value = data.external.secrets.result[secret.key]
    }
  }

  dynamic "secret" {
    for_each = local.database_url_v2_secret_values

    content {
      name  = secret.key
      value = secret.value
    }
  }

  registry {
    server               = azurerm_container_registry.main.login_server
    username             = azurerm_container_registry.main.admin_username
    password_secret_name = "registry-secret"
  }

  secret {
    name  = "registry-secret"
    value = azurerm_container_registry.main.admin_password
  }

  # build_and_publish_manual.yml deploys SHA-pinned images via
  # `az containerapp update --set-image SHA`; without this block tofu
  # apply reverts the live image back to ":latest", which silently rolls
  # the running revision to whatever ":latest" points to. Image lifecycle
  # is owned by the deploy pipeline, not Terraform.
  lifecycle {
    precondition {
      condition     = length(local.scribe_native_scheduler_env_name_collisions) == 0
      error_message = "Native Scribe and durable Zoom rollout settings cannot also be supplied through generic scheduler environment-name sets"
    }

    precondition {
      condition     = length(local.session_prep_scheduler_env_name_collisions) == 0
      error_message = "Session Prep rollout settings cannot also be supplied through generic scheduler environment-name sets"
    }

    precondition {
      condition     = local.database_url_configuration_valid
      error_message = "DATABASE_URL rollover configuration must select one configured database-url secret"
    }

    precondition {
      condition     = local.database_url_live_guard_valid
      error_message = "live DATABASE_URL state must already match the source-controlled selector"
    }

    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  tags = var.tags
}

resource "azurerm_container_app" "worker" {
  resource_group_name = azurerm_resource_group.main.name
  # NOTE location is determined automatically by the resource group

  name                         = "${azurerm_resource_group.main.name}-worker"
  container_app_environment_id = azurerm_container_app_environment.main.id

  # required for blue/green deployments
  revision_mode = "Single"

  # Only the worker performs transcript de-identification. Pin its exact
  # user-assigned identity rather than permitting an ambient credential chain.
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.health_services.id]
  }

  template {
    # without this, if the container fails, it will not be restarted even if tf config is updated
    min_replicas = 1
    # Worker has no HTTP ingress so KEDA has no implicit scaler to evaluate.
    # Pinning max == min sidesteps autoscaling, but Container Apps still
    # creates a ScaledObject under the hood — and Azure's KEDA upgrade on
    # 2026-05-19 made it strict about requiring at least one rule. Without
    # this stub, KEDA fails its periodic check (~every 30s) and after a
    # couple of failures deletes the ScaledObject, which Container Apps
    # interprets as "stop the container", causing a ~60s crash loop.
    max_replicas = 1

    custom_scale_rule {
      name             = "celery-cpu"
      custom_rule_type = "cpu"
      metadata = {
        type  = "Utilization"
        value = "80"
      }
    }

    container {
      name = "${local.base_name}-app-worker"

      # celery_healthcheck (registered in app/celery.py) exposes GET / on
      # port 9000 which calls inspect.ping() and returns 503 if the celery
      # worker is unresponsive. TCP probes only verify uvicorn holds the
      # port, which stays true even when celery itself has died — Azure
      # would see "healthy" and never restart. HTTP probes actually
      # validate celery is alive.
      #
      # Probe timeout default is 1s — too tight for inspect.ping() which
      # itself defaults to a 1s broker-round-trip timeout. We hit a
      # race during the 2026-05-21 deploy where every probe timed out
      # at exactly 1s. 10s gives inspect.ping room to respond plus
      # buffer for transient broker latency.
      readiness_probe {
        port             = 9000
        path             = "/"
        transport        = "HTTP"
        timeout          = 10
        interval_seconds = 10
      }

      startup_probe {
        port      = 9000
        path      = "/"
        transport = "HTTP"
        # Celery imports `app.models`, runs migrations, and starts a
        # FastAPI/uvicorn thread before / responds 200. Locally this can
        # take 15-25s on a cold start; allow 60s of probe time before
        # Azure considers startup failed.
        failure_count_threshold = 30
        interval_seconds        = 2
        timeout                 = 10
      }

      liveness_probe {
        port             = 9000
        path             = "/"
        transport        = "HTTP"
        timeout          = 10
        interval_seconds = 10
      }


      # the amount of time I wasted realizing that private images must be fully qualified, but public images do not need to be is embarrassing
      # https://github.com/hashicorp/terraform-provider-azurerm/issues/22459
      image = "${azurerm_container_registry.main.login_server}/cosmaneura/cosmaneura:latest"

      # https://learn.microsoft.com/en-us/azure/container-apps/health-probes?tabs=arm-template

      args = [data.external.procfile.result["worker"]]

      dynamic "env" {
        for_each = setunion(var.python_public_vars, var.python_billing_public_vars)

        content {
          name  = env.key
          value = data.external.secrets.result[env.key]
        }
      }

      dynamic "env" {
        for_each = local.zoom_durable_public_values

        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = merge(
          local.session_prep_shared_public_values,
          local.session_prep_authority_runtime_public_values,
        )

        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "AZURE_DEID_AUTH_MODE"
        value = var.enable_deid_managed_identity ? "managed_identity" : "client_secret"
      }

      env {
        name  = "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID"
        value = azurerm_user_assigned_identity.health_services.client_id
      }

      dynamic "env" {
        for_each = setunion(
          var.python_secret_vars,
          local.stripe_integration_enabled ? var.python_billing_secret_vars : toset([]),
        )

        content {
          name        = env.key
          secret_name = env.key == "DATABASE_URL" ? local.database_url_secret_name : replace(lower(env.key), "_", "-")
        }
      }

      # yes, memory is defined as a string and CPU is defined as a float
      cpu    = 1
      memory = "2Gi"
    }
  }

  dynamic "secret" {
    for_each = setunion(
      var.python_secret_vars,
      local.stripe_integration_enabled ? var.python_billing_secret_vars : toset([]),
    )

    content {
      name  = replace(lower(secret.key), "_", "-")
      value = data.external.secrets.result[secret.key]
    }
  }

  dynamic "secret" {
    for_each = local.database_url_v2_secret_values

    content {
      name  = secret.key
      value = secret.value
    }
  }

  # TODO could use identity based login in the future, but the Basic tier of acr doesn't support this
  # https://github.com/microsoft/NubesGen/blob/0d5d4896f5f2c93d1baca01807527bee56ff49b8/terraform/modules/container-apps/main.tf#L91
  registry {
    # unlike other registry configs, this does NOT contain a protocol
    server   = azurerm_container_registry.main.login_server
    username = azurerm_container_registry.main.admin_username

    # why is the password stored in a separate secret block? No idea. But, This is The Way.
    password_secret_name = "registry-secret"
  }

  secret {
    name  = "registry-secret"
    value = azurerm_container_registry.main.admin_password
  }

  # build_and_publish_manual.yml deploys SHA-pinned images via
  # `az containerapp update --set-image SHA`; without this block tofu
  # apply reverts the live image back to ":latest", which silently rolls
  # the running revision to whatever ":latest" points to. Image lifecycle
  # is owned by the deploy pipeline, not Terraform.
  lifecycle {
    precondition {
      condition     = length(local.scribe_native_worker_env_name_collisions) == 0
      error_message = "Native Scribe and durable Zoom rollout settings cannot also be supplied through generic worker environment-name sets"
    }

    precondition {
      condition     = length(local.session_prep_worker_env_name_collisions) == 0
      error_message = "Session Prep rollout settings cannot also be supplied through generic worker environment-name sets"
    }

    precondition {
      condition     = local.session_prep_authority_runtime_prerequisites_valid
      error_message = "Session Prep worker activation requires the exact canonical Clerk issuer"
    }

    precondition {
      condition     = local.deid_managed_identity_live_guard_valid
      error_message = "managed-identity activation requires the exact production worker identity, AnonymizerAPI resource, and realtime-only role assignment to exist first"
    }

    precondition {
      condition     = local.database_url_configuration_valid
      error_message = "DATABASE_URL rollover configuration must select one configured database-url secret"
    }

    precondition {
      condition     = local.database_url_live_guard_valid
      error_message = "live DATABASE_URL state must already match the source-controlled selector"
    }

    precondition {
      condition     = local.stripe_configuration_valid
      error_message = "Stripe integration/acquisition flags and required configuration are inconsistent"
    }

    ignore_changes = [
      template[0].container[0].image,
    ]
  }

  tags = var.tags
}

# TODO need to configure DynamicJsonColumns, but it's not supported yet
# https://github.com/microsoft/azure-container-apps/issues/938


####################
# Hosting Metadata
#
# - There are some manual DNS records that must be set which cannot be done by terraform. This information is included
#   in the note.
# - Various key names required for various scripts. Resource group, container names, etc.
#
####################

# https://registry.terraform.io/providers/1Password/onepassword/latest/docs/resources/item
# onepassword_item.squarespace_domain removed 2026-05-21 — see note above.
####################
# Health Services Access
####################

# Passwordless, app-only identity for calls from the platform web API to Scribe.
# Its Scribe.Workflow.Access application-role assignment is configured in Entra,
# outside Azure resource RBAC. No client secret is created or stored here.
resource "azurerm_user_assigned_identity" "scribe_workflow" {
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  name                = "${local.base_name}-scribe-workflow"

  tags = var.tags
}

resource "azurerm_user_assigned_identity" "health_services" {
  resource_group_name = azurerm_resource_group.main.name
  # location            = azurerm_resource_group.main.location
  location = "eastus"
  name     = "${local.base_name}-health-services"
}

# The worker uses only the realtime de-identification action. Scope that single
# data action to the existing AnonymizerAPI resource rather than to the ACR or
# subscription. The DeID service itself remains externally managed.
resource "azurerm_role_assignment" "health_services_deid_access" {
  scope                = local.deid_service_resource_id
  role_definition_name = "DeID Realtime Data User"
  principal_id         = azurerm_user_assigned_identity.health_services.principal_id
  principal_type       = "ServicePrincipal"
}

####################
# GHA Access
#
# Adapted from: https://github.com/likvid-bank/likvid-cloudfoundation/blob/86930f7b9aa538f7f8cb64074f351dfec81a4c84/kit/azure/buildingblocks/starterkit-composition/github-actions-terraform-setup/resources.azure.cicd.tf
# View all roles: az role definition list --output json --query '[].roleName' | fzf
####################

resource "azurerm_user_assigned_identity" "ghactions" {
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  name                = "${local.base_name}-gha-identity"
}

# see https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-azure#adding-the-federated-credentials-to-azure
resource "azurerm_federated_identity_credential" "ghactions" {
  parent_id           = azurerm_user_assigned_identity.ghactions.id
  resource_group_name = azurerm_resource_group.main.name

  name     = "github-actions"
  audience = ["api://AzureADTokenExchange"]
  issuer   = "https://token.actions.githubusercontent.com"

  # Desired state: bind Azure trust to the protected GitHub environment, not
  # merely to the branch ref. The live credential must first be bootstrapped
  # out of band and imported/adopted under the reviewed reconciliation hold in
  # infra/README.md; this resource cannot bootstrap its own OIDC access.
  # NOTE the casing here is very important; an exact mismatch fails auth.
  subject = "repo:CosmaNeura/cosmaneura:environment:production"
}

# push new containers to ACR
resource "azurerm_role_assignment" "github_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

# deploy images to container apps
# az role definition list --name "Container Apps Operator" --output json
resource "azurerm_role_assignment" "github_container_apps_operator" {
  scope                = azurerm_container_app_environment.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

resource "azurerm_role_assignment" "github_container_app_1_operator" {
  scope                = azurerm_container_app.app.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

resource "azurerm_role_assignment" "github_container_app_3_operator" {
  scope                = azurerm_container_app.worker.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ghactions.principal_id
}

# 'Container Apps Operator' is a built in role
# you can view these roles using: az role definition list | ov
# https://github.com/humanitec-architecture/reference-architecture-azure/blob/3de02801290cb8d49f5dfaa4565583e3945c7f2c/modules/github/azure.tf#L16
# https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/configuring-openid-connect-in-azure#adding-the-federated-credentials-to-azure

# onepassword_item.azure_github_actions removed 2026-05-21 — see note above.
