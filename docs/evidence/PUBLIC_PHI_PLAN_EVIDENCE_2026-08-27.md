# Public-PHI plan evidence — 2026-08-27

Scope: Priority 4 infrastructure/operations source review only
Base commit: `711682eb28180c2421689d589b5a74919c454644`
Tool: OpenTofu `1.12.5` on `darwin_arm64`
Production provider: AzureRM `4.73.0`
Staging provider: AzureRM `4.26.0`

No plan in this record was applied. No production plan was generated.

At `2026-08-28T01:37:43Z`, read-only state pulls recorded:

- production: lineage `bf7d0b48-f69c-2d83-3f87-f02d21f5aa21`, serial `277`, 48 top-level state resources;
- staging: lineage `42ae131c-250c-d321-f6c4-dae9e7ffb250`, serial `22`, 41 top-level state resources.

The state snapshots were created mode `0600`; their secret-bearing content was never printed or attached, and the raw snapshots were deleted after the redacted fields above were extracted. A future credentialed plan is invalid if either serial advances before review/apply.

Redacted state fields also confirm that both upload accounts and the shared state account record `allow_nested_items_to_be_public=true`, `shared_access_key_enabled=true`, and `public_network_access_enabled=true`, while HTTPS-only/TLS 1.2 are already active. Staging state serial 22 still tracks `azurerm_postgresql_flexible_server_firewall_rule.all` as `0.0.0.0`–`255.255.255.255`, even though the live control plane has only the Azure-services rule and exact `aca-staging-egress` address. This three-way source/state/live drift is why the import and refreshed staging plan are mandatory.

A separate read-only Container Apps query found the same single outbound address, `130.131.32.65`, on the staging app, legacy app, worker, and scheduler. The environment `staticIp` is `20.25.251.78` and is not the outbound allowlist value. AzureRM `4.26.0` exposes `azurerm_container_app.outbound_ip_addresses` as a computed list; the staging firewall precondition now requires the operator-supplied value to equal the refreshed scheduler's sole outbound address and fails closed if the list is empty or has changed cardinality.

## Validation

- Production root: `tofu -chdir=infra validate -no-color` — PASS. The shared diagnostic `metric` block emits a deprecation warning under AzureRM 4.73 because it intentionally retains compatibility with staging's pinned 4.26 provider.
- Staging root: `tofu -chdir=infra-staging validate -no-color` — PASS.
- Repository provider-mocked tests: `tofu -chdir=infra test -no-color -filter=tests/secretless_plan.tftest.hcl` — PASS, 3 passed / 0 failed.

## Isolated synthetic staging observability plan

Command shape:

```text
tofu -chdir=infra/modules/public_phi_observability test -no-color -verbose
```

The test uses staging's exact AzureRM `4.26.0` provider behind a mock, invalid-example resource IDs, `launch-ops@example.invalid`, and the synthetic role slug `platform-on-call`. It cannot read or change Azure.

Result at `2026-08-28T01:56:54Z`: **14 to add, 0 to change, 0 to destroy**; 1 passed / 0 failed.

Exact create action list:

```text
azurerm_monitor_action_group.public_phi_ops
azurerm_monitor_activity_log_alert.postgresql_admin_failure
azurerm_monitor_diagnostic_setting.postgresql_metrics
azurerm_monitor_diagnostic_setting.redis_metrics
azurerm_monitor_diagnostic_setting.upload_storage_metrics
azurerm_monitor_metric_alert.public_phi["postgresql_storage"]
azurerm_monitor_metric_alert.public_phi["postgresql_unavailable"]
azurerm_monitor_metric_alert.public_phi["redis_errors"]
azurerm_monitor_metric_alert.public_phi["redis_memory"]
azurerm_monitor_metric_alert.public_phi["upload_storage_availability"]
azurerm_monitor_scheduled_query_rules_alert_v2.public_phi["billing_webhook_failure"]
azurerm_monitor_scheduled_query_rules_alert_v2.public_phi["clinical_pipeline_failure"]
azurerm_monitor_scheduled_query_rules_alert_v2.public_phi["identity_control_failure"]
azurerm_monitor_scheduled_query_rules_alert_v2.public_phi["worker_backlog_failure"]
```

Assertions: three metric-only diagnostic settings, four aggregate fixed-event log alerts, five dependency/capacity metric alerts, one PostgreSQL administrative-failure activity alert, and all log queries terminate in only `failure_count = count()`.

Ephemeral full-log SHA-256 at validation time: `8bdecca2b1795d9eb4f9f69b7d4598ebf735d8f88490da02397ee2ed50814045`. The log remains outside source control and contains synthetic/mock values only.

## Credentialed staging plan attempt

A full staging refresh/plan was attempted with the same synthetic alert receiver and role slug. It stopped before producing an acceptable plan because:

- the fresh worktree's `.envrc` had not been trusted by `direnv`; and
- no 1Password provider/service-account credential was available to the process.

The partial plan file is invalid and must not be applied. It is not counted as plan evidence. This failure made no Azure or state change, and the partial plan and log were deleted after recording this blocker.

## Required next evidence

An authorized operator must provide the existing staging-only 1Password service-account context in a controlled runner, approve the reviewed `.envrc`, pass `staging_container_apps_egress_ipv4` from an immediately preceding read-only app query, and rerun a refreshed saved plan. The Terraform precondition must confirm that address exactly equals the refreshed scheduler's sole outbound address. Persist the approved observability enablement and owner values in the reviewed deployment variable source; inject the mailbox through the protected runner rather than a committed file or ad hoc long-lived shell history. Record the mode-0600 plan digest and complete action list. The only acceptable actions are the explicitly reviewed staging firewall adoption/storage flags and the final evidence-listed observability creates; any failed egress precondition, replacement, unexpected delete, production address, or other drift stops the rung.
