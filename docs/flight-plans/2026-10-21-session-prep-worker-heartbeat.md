# Session Prep fixed worker heartbeat

Risk: **Tier 2** — extends the explicitly bounded outbound telemetry boundary.

The existing 60-second no-argument heartbeat signals task execution through beat,
broker and worker. With global staging Sentry disabled, its former global-monitor
comment did not establish stopped-worker detection. This increment sends one
fixed check-in using the existing unbound health client, staging-only health
opt-in and DSN. No dependency, service, configuration, database or schema is added.

Only the fixed `session-prep-staging-worker` monitor, successful status, transport
UUID/time and literal cadence/margin configuration can cross this boundary.
Both queue and HTTP gates reject other fields, attachments, traces and monitor
names. The global SDK client and existing three category alerts retain their paths.

The Core69/Sovereignf4 staging rollout is complete. Live beta acceptance gaps
remain, including fresh brief readiness and external monitoring evidence. This
heartbeat increment remains unmerged and undeployed. Obtain independent review
after the final substantive push, then bind the reviewed source and verified
image through the existing staging process. No production release is included.

Technical acceptance additionally requires genuine ingestion and an enabled
monitor and notification route in the existing Sentry project. Existing Sentry
read access must be established before claiming a receipt or notification.

Before enabling the periodic publisher for this fixed monitor, perform a
separately approved two-pulse monitor rehearsal using the accepted, unbound
health client and fixed check-in payload. Verify that no periodic publisher is
already sending to this monitor. Verify actual ingestion of the first pulse,
then intentionally send no next pulse and observe the missed-check-in state and
notification. Send the second pulse and verify actual ingestion, monitor
recovery and recovery notification. Ordinary Core and Scribe processing
continues throughout; do not pause either product or disable the shared health
flag. If an existing periodic publisher prevents the missing interval, this
rehearsal is inapplicable rather than permission to interrupt it.

After releasing the heartbeat, independently observe recurring accepted
check-ins from the ordinary 60-second beat/broker/worker task. Bind that evidence
to the deployed source and image. A direct rehearsal pulse does not prove this
execution-path wiring. The two-pulse rehearsal proves Sentry ingestion and
missed-notification/recovery handling for a synthetic gap; neither check claims
actual stopped-worker or per-replica fault injection. One-minute cadence plus
two-minute margin is approximately three minutes plus service/notification
latency; measure actual timestamps rather than promise that deadline. The
heartbeat does not identify the failed component or establish clinical
generation health.

`SESSION_PREP_HEALTH_ALERTS_ENABLED` is an exact staging-only opt-in shared with
all three category alerts. It is not a heartbeat-only pause control. Global
staging Sentry remains disabled. Source tests, a successful CLI command, local
capture or SDK flush do not establish ingestion or notification delivery. This
flight plan authorizes no live pulse, monitor/rule change or recipient change.

Recovery uses a retained compatible frontier-aware Core image, preserving the
correction-frontier schema and data. Never restore old8d after an acknowledged
correction. If the fixed monitor remains enabled, its recovery image must also
support the accepted heartbeat path. A frontier-aware image without heartbeat
support requires a separate monitoring decision; additive schema preservation
alone is not monitoring recovery. Do not disable the shared health flag as a
heartbeat test or recovery shortcut. No automatic rollback, Core/Scribe pause
or live change is performed by this code or its offline tests.
