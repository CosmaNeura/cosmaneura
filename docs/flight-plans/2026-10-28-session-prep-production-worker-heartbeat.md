# Session Prep production worker heartbeat

Risk: **Tier 2** — adds a new default-off production telemetry boundary and a
production infrastructure control. This source checkpoint does not configure
Sentry, send a check-in, deploy, enable production, or change staging.

## Closed source boundary

The existing 60-second no-argument heartbeat task now invokes a second,
production-only publisher after the unchanged staging publisher. The new path
requires `PYTHON_ENV=production`, the exact global product gate
`SESSION_PREP_PROVIDER_ENABLED=true`, and the independent exact string
`SESSION_PREP_PRODUCTION_HEARTBEAT_ENABLED=true`. Terraform supplies the latter
worker-only setting from
`enable_session_prep_production_worker_heartbeat`, whose default is `false`.
The API and scheduler do not receive it. A disabled or non-production process
does not read the DSN or construct a client. The worker resource also refuses a
plan that enables this heartbeat while
`session_prep_production_rollout.enabled` is false, so configuration drift is
blocked both by Terraform and by the runtime product-gate check. The separate
heartbeat switch remains a fast stop that can turn monitoring off without
turning Session Prep off.

The publisher uses the worker's existing `SENTRY_DSN` through a separate,
unbound SDK client. It never initializes, replaces, scopes, or flushes the
global production Sentry client. Its transport accepts one envelope item and
one exact payload shape:

- monitor slug `session-prep-production-worker`;
- status `ok`, duration `0.0`, environment `production`;
- release label `session-prep-production-health`;
- fixed one-minute UTC schedule, two-minute margin, one-minute maximum runtime,
  and one-event failure/recovery thresholds;
- generated transport/check-in UUIDs and an ISO UTC send time.

No record, provider, patient, appointment, task, exception, log, breadcrumb,
trace, attachment, arbitrary tag, host name, or caller-controlled value is
accepted. Unknown fields, duplicate JSON keys, non-check-in items, malformed
timestamps, payloads over 1 KiB, non-HTTPS DSNs, redirects, retries, and
noncanonical values are dropped. Transport failure never fails or retries the
ordinary heartbeat task.

The fixed release label does **not** identify the deployed Core image. Release
evidence must separately bind the check-in to the exact source SHA, immutable
image digest, worker revision, configuration, and observation window.

## Read-only preflight

Keep the Terraform switch false while preparing this evidence:

1. Pin the reviewed Core source and immutable worker image. Verify that the
   candidate contains this heartbeat path and the production Session Prep
   worker/runtime gates.
2. Produce a saved OpenTofu plan only. Require exactly one new worker env value,
   still `false`, and no API, scheduler, schema, identity, network, recipient,
   or unrelated change.
3. Verify the current worker has the existing production `SENTRY_DSN` secret,
   but never print, export, compare, or copy its value. This checkpoint adds no
   secret and no new vendor.
4. In the existing production Sentry project, verify that the fixed monitor
   slug is unused. An existing publisher or monitor with that slug blocks this
   sequence; do not interrupt or reuse it by assumption.
5. Identify the approved operational destination and named owner for failure
   and recovery. Do not infer a recipient from staging, a personal address, or
   an existing unrelated rule.
6. Confirm retained, migration-compatible worker and scheduler revisions and
   that disabling only this monitor/switch is an accepted fast stop.

## Founder-supplied activation inputs

Activation remains founder-only and requires one reviewed release record with:

- exact Core source SHA, immutable image digest, and worker revision;
- saved production plan with the heartbeat switch changing `false` to `true`;
- production Sentry project/monitor identity;
- explicit failure and recovery rule definitions and approved destination;
- acknowledgement that unchanged project-wide Sentry rules may also notify;
- exact synthetic rehearsal window and recovery owner;
- final apply/deploy authorization coordinated with Session Prep activation.

This heartbeat must not be enabled for a worker image lacking the publisher or
while production Session Prep remains dark. It is a health prerequisite, not a
license to bypass the ordinary provider/practice/BAA/entitlement onboarding and
access lifecycle or handle production chart data.

## Activation and acceptance

Create or configure only the fixed monitor and its two rules before enabling
the publisher. The intended monitor configuration is interval one minute,
grace two minutes, maximum runtime one minute, failure threshold one, and
recovery threshold one. Failure should notify on a newly missed check-in (or a
resolved monitor regressing); recovery should notify when that issue resolves.
Keep rule scope fixed to this monitor and preserve every existing monitor,
rule, and recipient.

After the separately approved image/configuration rollout:

1. Observe a recurring accepted check-in from the ordinary
   beat/broker/worker task and bind it to the deployed worker revision.
2. During the approved synthetic rehearsal, pause only this fixed publisher or
   monitor input by the pre-reviewed mechanism; never stop Core, Scribe, the
   broker, or production chart processing merely to manufacture evidence.
3. Verify the actual missed state and actual destination delivery. Rule history
   or an SDK flush alone is not a notification receipt.
4. Restore the publisher and verify an accepted check-in, resolved monitor
   state, and actual recovery delivery.
5. Record observed timestamps. A one-minute cadence plus two-minute margin is
   approximately three minutes plus vendor/notification latency, not a hard
   SLA and not per-replica coverage.

The check-in proves that at least one scheduler/broker/worker path is executing.
It does not prove every worker replica, model-provider health, queue latency,
generation correctness, or Session Prep UI availability. Those remain separate
release gates.

## Recovery

Fast stop is a reviewed plan setting
`enable_session_prep_production_worker_heartbeat=false`, followed by disabling
only `session-prep-production-worker` if the deployed image can no longer
publish. Preserve the existing global Sentry client, DSN, monitors, recipients,
and Session Prep records. Never leave the monitor enabled over an image without
the publisher because that creates false outages. Never treat removing this
source commit as sufficient recovery from a live monitor/configuration change.

This checkpoint is integrated onto the final automatic-access activation base
and is the combined independent-review target. Its worker environment block and
mocked OpenTofu plans must pass on that exact combined SHA before publication.
No live action is authorized by this flight plan.
