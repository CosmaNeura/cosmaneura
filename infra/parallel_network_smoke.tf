locals {
  parallel_network_smoke_enabled = (
    var.deploy_parallel_vnet_environment &&
    var.deploy_parallel_network_smoke_job
  )
}

resource "terraform_data" "parallel_network_smoke_guard" {
  count = var.deploy_parallel_network_smoke_job ? 1 : 0

  lifecycle {
    precondition {
      condition     = var.deploy_parallel_vnet_environment
      error_message = "deploy_parallel_network_smoke_job requires deploy_parallel_vnet_environment=true"
    }

    precondition {
      condition     = var.parallel_network_smoke_image_tag != "not-built"
      error_message = "build an immutable smoke image and set parallel_network_smoke_image_tag before enabling the job"
    }

    precondition {
      condition     = var.parallel_scribe_private_base_url != ""
      error_message = "parallel_scribe_private_base_url is required when the smoke job is enabled"
    }
  }
}

resource "azurerm_role_assignment" "parallel_network_smoke_acr_pull" {
  count = local.parallel_network_smoke_enabled ? 1 : 0

  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.scribe_workflow.principal_id

  depends_on = [terraform_data.parallel_network_smoke_guard]
}

resource "azurerm_container_app_job" "parallel_network_smoke" {
  count = local.parallel_network_smoke_enabled ? 1 : 0

  name                         = "${local.base_name}-net-smoke"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = var.parallel_platform_location
  container_app_environment_id = azurerm_container_app_environment.parallel_platform[0].id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 300
  replica_retry_limit          = 0

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.scribe_workflow.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.scribe_workflow.id
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name   = "network-smoke"
      image  = "${azurerm_container_registry.main.login_server}/${var.parallel_network_smoke_image_repository}:${var.parallel_network_smoke_image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "SCRIBE_HEALTH_URL"
        value = "${trimsuffix(var.parallel_scribe_private_base_url, "/")}/healthz"
      }

      env {
        name  = "SCRIBE_UNAUTHENTICATED_WORKFLOW_URL"
        value = "${trimsuffix(var.parallel_scribe_private_base_url, "/")}/v1/consents"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.parallel_network_smoke_acr_pull,
    terraform_data.parallel_network_smoke_guard,
  ]

  tags = merge(var.tags, {
    purpose = "parallel-platform-synthetic-validation"
  })
}
