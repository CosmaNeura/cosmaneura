# Flight plan: Zoom/Recall operational stabilization

## Outcome and boundaries

- Tier 2 outcome: one synthetic staging appointment produces exactly one authorized Recall capture; missed joins and dispatch failures produce PHI-scrubbed Sentry issues and a verified human notification.
- Scope: #239 alerting, watchdog, dependency/ingress probes, and this plan.
- Excluded: Auth/IAM, #238, #241, deployment/OIDC/secrets, Scribe, Session Prep/Sovereign, #246, production deployment, and outside-party contact.
- Use synthetic identities, meetings, media, and text only. Never put raw IDs, names, content, tokens, or secrets in logs, Sentry, GitHub, or evidence.

## Release controls and prerequisites

Canonical sequence:

`approved source SHA -> staging-specific immutable image/digest -> staging evidence -> explicit production promotion`

- Record both staging and production image digests from the same source SHA; frontend build-time configuration requires separate images.
- Hold for the exact approved Auth base SHA, then normally restack, rerun CI, and refresh independent review at the resulting SHA. Never force-push.
- Hold #241; this release has no schema or migration. Agents do not deploy.
- Attest that staging and every connected account are synthetic-only.
- Use a dedicated staging Zoom app/account, Recall credential, Clerk provider, wrong-tenant/view-as actor, synthetic appointment, and notification route.
- Record read-only access to API, worker, scheduler, queue, Redis, database, encrypted-inbox health, and the prior known-good staging digest.
- Resolve the telemetry gate first: Sentry intentionally initializes only in production, so staging alert delivery needs a separately approved sink.
- Receiver-first rollout: deploy with `ZOOM_WATCHDOG_ENABLED=false`; verify every worker runs the exact SHA, old revisions are drained, and neither pre-record-ID watchdog intents nor legacy `app.jobs.verify_bot_joined.perform` tasks exist; only then enable the producer.
- Never print credentials or borrow production credentials/configuration.

## Reliability model

- Every watchdog check is an encrypted, meeting-record/occurrence/check-scoped inbox intent; Celery receives only its opaque delivery ID. Database uniqueness and fenced claims absorb duplicate/lost replies while the reconciler retries only that observer, never the original Zoom event or provider side effects.
- Failed checks enter bounded retry state; exhaustion becomes visible dead-letter state and requires the existing authorized redrive control.
- The alert marker follows local SDK acceptance; it does not prove Sentry receipt or human notification, which the staging gate verifies end to end.
- A self-signed ingress probe proves TLS/routing, local signer-verifier consistency, and durable-inbox acceptance only. Only a provider-originated callback proves the Zoom Marketplace secret matches.

## Staging evidence

1. Freeze the approved SHA; pass focused/full tests, static checks, the change budget, and independent exact-head review.
2. Build and record the staging digest; the release lead deploys it and checks API, worker, scheduler, queues, Redis, database, and inbox health.
3. Sign in through synthetic Clerk, prove session persistence and the expected practice, then prove wrong-tenant and view-as access are denied.
4. Connect the staging Zoom account through OAuth without exposing secrets.
5. Start one synthetic occurrence and retain sanitized proof that staging accepted a provider-originated signed webhook.
6. Confirm exact appointment/provider/practice/occurrence binding and one-use authorization before bot creation.
7. Admit one synthetic guest; verify exactly one bot joins, records, and leaves and that native consent plus chat disclosure precede recording.
8. Speak a fixed synthetic phrase; verify one recording/transcript attaches only to the bound appointment.
9. Replay duplicate and reordered sanitized callbacks; verify idempotent bot, recording, transcript, binding, and lifecycle counts.
10. Revoke staging OAuth/access and prove a later occurrence creates no bot.
11. Force a no-join and verify the reason-coded scrubbed Sentry issue, human notification, durable retry after broker failure, and no stuck intent.
12. Observe for 30 minutes; retain SHA/digest, timestamps, sanitized event types, expected counts, CI/review URLs, and workload health—never payloads.

## Abort and recovery

Abort for SHA/digest mismatch, failed CI/review, unproved provider callback, tenant/view-as bypass, missing/late disclosure, duplicate bots or recordings, wrong attachment, capture after revocation, absent notification, PHI-bearing telemetry, unhealthy workloads/queues, or unresolved/dead-letter intent.

- Stop the synthetic meeting and prevent a new occurrence.
- Do not manually delete Redis ownership keys or encrypted inbox rows; inspect pseudonymous references and use the authorized inbox redrive if required.
- The release lead rolls staging back to the recorded digest. Preserve sanitized evidence and rerun the failed rung before any promotion.
- Before rollback, disable the producer, stop new synthetic meetings, wait out the 15-minute watchdog window, and prove internal intents plus queues are drained; only then restore old workers.

## Outside-party gate

Provide the release lead/founder the exact SHA/digests, evidence matrix, credential checklist, recovery owners, and meeting script: sign in; confirm tenant; connect Zoom; verify disclosure; run one bound synthetic visit; admit one guest; speak the fixed phrase; verify one bot/recording/transcript and exact attachment; end the meeting; confirm bot departure and workload health.

Do not contact or schedule the outside party. Pilot communication and production promotion remain explicit founder/release-lead decisions.
