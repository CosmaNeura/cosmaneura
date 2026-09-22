output "diagnostic_setting_count" {
  description = "Number of metric-only data-service diagnostic settings."
  value       = 3
}

output "log_alert_count" {
  description = "Number of aggregate fixed-event log alerts."
  value       = length(azurerm_monitor_scheduled_query_rules_alert_v2.public_phi)
}

output "metric_alert_count" {
  description = "Number of dependency-health and capacity metric alerts."
  value       = length(azurerm_monitor_metric_alert.public_phi)
}

output "activity_log_alert_count" {
  description = "Number of control-plane administrative failure alerts."
  value       = 1
}

output "all_log_queries_are_aggregate_only" {
  description = "True when every alert query emits only the required aggregate count."
  value = alltrue([
    for alert in values(local.log_alerts) : endswith(trimspace(alert.query), "| summarize failure_count = count()")
  ])
}
