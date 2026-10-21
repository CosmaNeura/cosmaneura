# Type-identifiable Session Prep runtime alerts

Tier 2: monitoring across the patient-data boundary. Core-only, one deployable
outcome; no schema, dependency, configuration or authorization change.

Preparation maintenance currently logs a warning for queue lag, recent failures
or maintenance outage. The existing Sentry scrubber removes free-text messages,
tags and fingerprints, so these logs do not supply a fixed alert category.

Add three exact Session Prep-only exception categories through the existing
Sentry capture path: stalled queue, recent actionable build failures and
maintenance unavailable. Use the existing 120-second queue age and health
failure window. Only enabled preparation reports degraded health; receipt
maintenance continues, and its failure can be reported, even with generation
off. No clinician, record, source, count or caught exception is passed to Sentry.

A local scope event processor rebuilds the event from its fixed category before
the unchanged global scrubber. It removes inherited request/exception/breadcrumb
context and clears the SDK hint so inherited attachments cannot leave. The new
unraised exception has no message, traceback, cause or context. A fresh random
event identifier is SDK transport metadata only; no job returns or logs it.
Monitoring failure does not interrupt maintenance or introduce a retry. The
minute scheduler emits at most two health categories per run; no new storage or
cross-process deduplication is introduced. Sentry groups these by fixed type.

Validate actual SDK capture using an in-memory transport and the real scrubber,
including hostile inherited context/attachments, raw maintenance failures, healthy/disabled
controls, exact thresholds, and capture/scope/config failure safety. Keep SDK
network transport and global test startup disabled in the local runner.

Independent review is required after final substantive push. The parent owns
release selection/deployment; recovery restores the previous immutable Core
image. The unchanged existing Sentry enablement must be verified separately.
These tests prove local event creation and sanitization, not Sentry ingestion,
configured alert rules, subscriber delivery or scheduler heartbeat detection.
If the scheduler never runs, this in-job event path cannot report that outage.
