mock_provider "azurerm" {
  mock_resource "azurerm_monitor_action_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Insights/actionGroups/mock"
    }
  }
}

run "synthetic_staging_activation_plan" {
  command = plan

  variables {
    environment                = "staging"
    resource_prefix            = "cosmaneura-staging"
    resource_group_id          = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock"
    resource_group_name        = "cosmaneura-staging"
    location                   = "northcentralus"
    log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.OperationalInsights/workspaces/mock"
    postgresql_server_id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.DBforPostgreSQL/flexibleServers/mock"
    redis_cache_id             = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Cache/Redis/mock"
    upload_storage_account_id  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/mock/providers/Microsoft.Storage/storageAccounts/mock"
    alert_email                = "launch-ops@example.invalid"
    support_owner              = "platform-on-call"
    tags = {
      environment = "staging"
      managed     = "true"
    }
  }

  assert {
    condition     = output.diagnostic_setting_count == 3
    error_message = "expected exactly three metric-only diagnostic settings"
  }

  assert {
    condition     = output.log_alert_count == 4
    error_message = "expected exactly four aggregate log alerts"
  }

  assert {
    condition     = output.metric_alert_count == 5
    error_message = "expected exactly five metric alerts"
  }

  assert {
    condition     = output.activity_log_alert_count == 1
    error_message = "expected exactly one PostgreSQL administrative-failure alert"
  }

  assert {
    condition     = output.all_log_queries_are_aggregate_only
    error_message = "all log alert queries must emit only failure_count"
  }
}
