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

Release only after the current Core69/Sovereignf4 rollout is reconciled. Obtain
independent final-head review, then bind the reviewed source and verified image
through the existing staging process. Do not advance the current rollout's source
gate while it is active. No production release is part of this increment.

Technical acceptance additionally requires genuine ingestion and an enabled
monitor/notification in the existing Sentry project. Verify the first check-in,
then an explicitly approved, brief heartbeat-emission pause while ordinary Core
and Scribe processing continues. Observe the missed-check-in notification and
subsequent recovery. One-minute cadence plus two-minute margin is approximately
three minutes plus service/notification latency; this timing is not locally proven.
The signal identifies a broken execution path, not which component failed or
whether clinical generation is healthy.

Recovery uses a retained compatible frontier-aware Core image, preserving the
correction-frontier schema and data. Never restore old8d after an acknowledged
correction. If necessary, disable only the existing worker health opt-in and
coordinate pausing this fixed server-side monitor to prevent expected false
alerts while rolling forward. This also suspends the three narrow category
alerts; global Sentry stays disabled. No automatic rollback or live change is
performed by this code or by its offline tests.
