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

This increment is now integrated locally on the actual merged Core base
`c99da13b0f41d050e8c9fede9ce5266b4730dadb` (PR813), preserving its correction
frontier, receiver/publication and private-note recovery changes. Its product
code and committed tests match PR807's reviewed source
`5471e4f209d9f2bde206a0b11c99cf74f6d4d8cc`; only this flight plan is refreshed.
The original PR807 branch and the separate prepared c99 staging image are
unchanged. This new integration endpoint is not yet pushed, reviewed or
deployed. Obtain independent review after its final substantive push, then bind
the reviewed merged source and a newly verified image through the staging
process. Live beta acceptance gaps, including external monitoring evidence,
remain. No production release is included.

Technical acceptance additionally requires genuine ingestion and an enabled
monitor and notification route in the existing Sentry project. Existing Sentry
read access must be established before claiming a receipt or notification.

The current staging worker's shared health flag is already exactly `true`, with
global staging Sentry disabled. A later heartbeat image replacement preserving
that configuration starts check-ins on the existing 60-second task immediately;
there is no separate heartbeat activation flag. Confirm these managed settings
and deployed identity again before that release. Prepare and test the monitor
and routes first; do not deploy heartbeat code to create an artificial gap.

Before that release, obtain one explicit staging-only authorization for the
fixed monitor configuration, exactly three content-free check-ins and planned
failure/recovery notifications, including possible notifications from unchanged
shared project rules. Use the accepted unbound health client and exact fixed
payload, without application bootstrap or periodic registration. Verify that no
existing periodic publisher already uses this monitor; otherwise the rehearsal
is inapplicable and does not permit interrupting it. Follow this sequence:

1. Send one bootstrap `ok` check-in to create the exact
   `session-prep-staging-worker` slug. Verify actual ingestion and the new
   monitor's identity, code-managed settings and observed Disable/Enable
   controls before changing its mode.
2. Disable only this new monitor while preparing its fixed UTC schedule:
   interval 1 minute, grace 2 minutes, max runtime 1 minute, failure tolerance 1
   and recovery tolerance 1.
3. Create exactly two staging rules using **Alert on specific monitors → Edit
   Monitors**, selecting only the actually created fixed monitor. Route failure
   on a new issue or a resolved issue regressing, and recovery on an issue
   resolving, to the existing CosmaNeura `#site-bots` channel. Set each rule's
   per-issue action throttle independently to 5 minutes. Preserve all existing
   rules, recipients and monitors; do not select All Issues, `sync_clerk`, the
   global heartbeat or another monitor. Existing project rules may additionally
   send shared-channel Slack or assignee/recently-active-member email.
4. Enable only this monitor, send the first rehearsal `ok` pulse and verify
   ingestion. Send no intervening pulse; observe actual missed state and
   failure delivery at the intended destination. Send the second rehearsal
   `ok` pulse; verify ingestion, resolved/recovered state and recovery delivery.
   Capture or flush success and rule history alone are not destination receipts.
5. After recovery, disable only this monitor until the separately reviewed
   heartbeat release is ready to activate recurring monitoring. The test's
   temporary inactive state is not operational-beta completion.

Ordinary Core and Scribe processing continues throughout; do not pause either
product or disable the shared health flag. An uncertain send does not authorize
automatic replay beyond the three approved check-ins. This rehearsal excludes
fixed error-category events and production.

Coordinate enabling this fixed monitor with the later heartbeat image release,
then independently observe recurring accepted check-ins from the ordinary
60-second beat/broker/worker task. Bind that evidence to the deployed source,
image and current worker; the fixed `session-prep-health` release label alone
cannot identify them. A direct rehearsal pulse does not prove this execution
path. The two rehearsal pulses prove Sentry ingestion and
missed-notification/recovery handling for a synthetic gap; neither check claims
actual stopped-worker or per-replica fault injection. A shared-queue heartbeat
establishes execution by at least one worker, not every replica. One-minute
cadence plus two-minute margin is approximately three minutes plus service/notification
latency; measure actual timestamps rather than promise that deadline. The
heartbeat does not identify the failed component or establish clinical
generation health.

`SESSION_PREP_HEALTH_ALERTS_ENABLED` is an exact staging-only opt-in shared with
all three category alerts. It is not a heartbeat-only pause control. Global
staging Sentry remains disabled. Source tests, a successful CLI command, local
capture or SDK flush do not establish ingestion or notification delivery. This
flight plan authorizes no live pulse, monitor/rule change or recipient change.

Recovery must preserve the current correction frontier and acknowledged
receiver/private-note data. Never restore old8d after an acknowledged correction.
Choose and verify a retained image containing the compatible c99 changes and
accepted heartbeat path if this monitor stays enabled. The separate prepared
c99 image and the prior Core04/2d9 image contain no fixed heartbeat publisher;
falling back to either therefore requires an explicitly authorized decision to
disable only this monitor, followed by re-enabling it when heartbeat-capable
service returns. c99 is the conservative fallback for preserving all current
product changes; prior Core04 needs a separate feature/data compatibility
decision. Leaving the monitor enabled over either non-heartbeat image would
create false missed alerts. Additive schema preservation alone is not monitoring
recovery. Keep the shared health flag and its three category alerts intact. No
automatic rollback, Core/Scribe pause or live change is performed by this code
or its offline tests.
