# Phase-one, default-off observability baseline. It is deliberately partial:
# backup-rehearsal freshness, Container Apps restart/capacity, queue age/depth,
# scheduler heartbeat, additional database/Redis load, and storage-capacity
# signals still require verified source telemetry and thresholds before this
# module can satisfy the public-PHI alert-coverage activation gate.
locals {
  alert_tags = merge(var.tags, {
    control_scope = "public-phi-operations"
    support_owner = var.support_owner
  })

  # Every query filters only fixed event names/codes and emits one aggregate
  # count. No message body, patient/provider identifier, payload, prompt,
  # transcript, note, filename, URL, or free-text exception reaches an alert.
  log_alerts = {
    identity_control_failure = {
      severity    = 1
      display     = "Identity and entitlement control failure"
      description = "Fixed-code identity or entitlement control failures were recorded."
      query       = <<-KQL
        ContainerAppConsoleLogs_CL
        | where Log_event_code_s in (
            "clerk_email_lookup_failed",
            "pilot_access_identity_verification_failed",
            "managed_organization_identity_verification_failed",
            "account_identity_recovery_required"
          )
        | summarize failure_count = count()
      KQL
    }
    billing_webhook_failure = {
      severity    = 1
      display     = "Billing or webhook control failure"
      description = "A fixed billing/webhook failure event or durable-inbox operator condition was recorded."
      query       = <<-KQL
        ContainerAppConsoleLogs_CL
        | where Log_event_s in (
            "stripe webhook signature verification failed",
            "stripe webhook received while billing rollout is not ready",
            "durable webhook handler failed",
            "external webhook inbox requires operator attention",
            "account closure is applied but Stripe seat reconciliation was not queued; verify account and billing state before retrying"
          )
        | summarize failure_count = count()
      KQL
    }
    worker_backlog_failure = {
      severity    = 2
      display     = "Worker failure or durable backlog"
      description = "A fixed Celery failure code or durable-inbox operator condition was recorded."
      query       = <<-KQL
        ContainerAppConsoleLogs_CL
        | where Log_event_code_s == "task_failed"
            or Log_event_s == "external webhook inbox requires operator attention"
        | summarize failure_count = count()
      KQL
    }
    clinical_pipeline_failure = {
      severity    = 1
      display     = "Upload, transcription, or note pipeline failure"
      description = "A fixed-code or fixed-message clinical pipeline failure was recorded."
      query       = <<-KQL
        ContainerAppConsoleLogs_CL
        | where Log_event_code_s in (
            "clinical_upload_claim_release_failed",
            "clinical_upload_receipt_completion_deferred",
            "example_note_upload_claim_release_failed",
            "example_note_upload_receipt_completion_deferred",
            "deidentification_request_failed",
            "visit_note_finalization_scope_invalid"
          )
          or Log_event_s in (
            "transcription failed",
            "speaker identification failed"
          )
        | summarize failure_count = count()
      KQL
    }
  }

  metric_alerts = {
    postgresql_unavailable = {
      scope       = var.postgresql_server_id
      namespace   = "Microsoft.DBforPostgreSQL/flexibleServers"
      metric      = "is_db_alive"
      aggregation = "Minimum"
      operator    = "LessThan"
      threshold   = 1
      severity    = 0
      description = "PostgreSQL reported unavailable during the evaluation window."
    }
    postgresql_storage = {
      scope       = var.postgresql_server_id
      namespace   = "Microsoft.DBforPostgreSQL/flexibleServers"
      metric      = "storage_percent"
      aggregation = "Maximum"
      operator    = "GreaterThan"
      threshold   = 80
      severity    = 1
      description = "PostgreSQL storage utilization exceeded 80 percent."
    }
    redis_memory = {
      scope       = var.redis_cache_id
      namespace   = "Microsoft.Cache/redis"
      metric      = "usedmemorypercentage"
      aggregation = "Maximum"
      operator    = "GreaterThan"
      threshold   = 80
      severity    = 1
      description = "Redis memory utilization exceeded 80 percent."
    }
    redis_errors = {
      scope       = var.redis_cache_id
      namespace   = "Microsoft.Cache/redis"
      metric      = "errors"
      aggregation = "Total"
      operator    = "GreaterThan"
      threshold   = 0
      severity    = 1
      description = "Redis emitted one or more service errors."
    }
    upload_storage_availability = {
      scope       = var.upload_storage_account_id
      namespace   = "Microsoft.Storage/storageAccounts"
      metric      = "Availability"
      aggregation = "Minimum"
      operator    = "LessThan"
      threshold   = 99
      severity    = 1
      description = "Upload storage availability fell below 99 percent."
    }
  }
}

resource "azurerm_monitor_action_group" "public_phi_ops" {
  name                = "${var.resource_prefix}-public-phi-ops"
  resource_group_name = var.resource_group_name
  short_name          = substr("phi-${var.environment}", 0, 12)
  enabled             = true
  tags                = local.alert_tags

  email_receiver {
    name                    = "public-phi-role-mailbox"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

resource "azurerm_monitor_diagnostic_setting" "postgresql_metrics" {
  name                       = "public-phi-metrics"
  target_resource_id         = var.postgresql_server_id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  # `metric` retains compatibility with staging's pinned azurerm 4.26 while
  # remaining accepted by production's 4.73 provider.
  metric {
    category = "AllMetrics"
    enabled  = true
  }
}

resource "azurerm_monitor_diagnostic_setting" "redis_metrics" {
  name                       = "public-phi-metrics"
  target_resource_id         = var.redis_cache_id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  metric {
    category = "AllMetrics"
    enabled  = true
  }
}

resource "azurerm_monitor_diagnostic_setting" "upload_storage_metrics" {
  name                       = "public-phi-metrics"
  target_resource_id         = var.upload_storage_account_id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  metric {
    category = "Transaction"
    enabled  = true
  }

  metric {
    category = "Capacity"
    enabled  = true
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "public_phi" {
  for_each = local.log_alerts

  name                = "${var.resource_prefix}-${replace(each.key, "_", "-")}"
  display_name        = each.value.display
  description         = each.value.description
  resource_group_name = var.resource_group_name
  location            = var.location
  scopes              = [var.log_analytics_workspace_id]
  severity            = each.value.severity
  enabled             = true

  evaluation_frequency    = "PT5M"
  window_duration         = "PT10M"
  auto_mitigation_enabled = true

  criteria {
    query                   = each.value.query
    metric_measure_column   = "failure_count"
    operator                = "GreaterThan"
    threshold               = 0
    time_aggregation_method = "Total"

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.public_phi_ops.id]
    custom_properties = {
      runbook       = "docs/PUBLIC_PHI_INCIDENT_RESPONSE.md"
      support_owner = var.support_owner
    }
  }

  tags = local.alert_tags
}

resource "azurerm_monitor_metric_alert" "public_phi" {
  for_each = local.metric_alerts

  name                = "${var.resource_prefix}-${replace(each.key, "_", "-")}"
  resource_group_name = var.resource_group_name
  scopes              = [each.value.scope]
  description         = each.value.description
  severity            = each.value.severity
  enabled             = true
  auto_mitigate       = true
  frequency           = "PT1M"
  window_size         = "PT5M"
  tags                = local.alert_tags

  criteria {
    metric_namespace = each.value.namespace
    metric_name      = each.value.metric
    aggregation      = each.value.aggregation
    operator         = each.value.operator
    threshold        = each.value.threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.public_phi_ops.id
  }
}

# Azure Database for PostgreSQL exposes no single portable "restore failed"
# metric across the pinned provider versions. Capture failed administrative
# operations at the resource-group boundary instead. The notification carries
# control-plane metadata only; the restore rehearsal remains the recovery proof.
resource "azurerm_monitor_activity_log_alert" "postgresql_admin_failure" {
  name                = "${var.resource_prefix}-postgresql-admin-failure"
  resource_group_name = var.resource_group_name
  location            = "global"
  scopes              = [var.resource_group_id]
  description         = "A PostgreSQL Flexible Server administrative operation failed; follow the public-PHI incident runbook."
  enabled             = true
  tags                = local.alert_tags

  criteria {
    category          = "Administrative"
    resource_provider = "Microsoft.DBforPostgreSQL"
    resource_type     = "Microsoft.DBforPostgreSQL/flexibleServers"
    status            = "Failed"
  }

  action {
    action_group_id = azurerm_monitor_action_group.public_phi_ops.id
  }
}
