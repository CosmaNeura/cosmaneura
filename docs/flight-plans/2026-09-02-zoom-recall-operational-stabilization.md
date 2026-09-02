# Flight plan: Zoom/Recall operational stabilization

## Outcome and boundaries

- Outcome: a synthetic staging appointment produces exactly one authorized
  Recall capture, while missed joins and dispatch failures become PHI-scrubbed
  Sentry issues and human notifications.
- Scope: PR #239 alerting, join watchdog, dependency/ingress probes, and this
  non-PHI acceptance plan.
- Non-goals: Auth/IAM changes, #238, dormant occurrence schema #241, deployment
  workflow or secret changes, Scribe, Session Prep/Sovereign, #246, production
  deployment, and contact with an outside party.
- Data boundary: synthetic identities, appointment, meeting, audio, and text
  only. Never copy raw IDs, participant names, content, tokens, or secrets into
  logs, Sentry, GitHub, screenshots, or this evidence record.

## Risk and dependencies

- Tier: **Tier 2**. A bad rollout can silently miss a visit, create duplicate
  recordings, attach content to the wrong appointment, or leak identifiers.
- Initial PR head: `7eed88b34a1f147b9cb7161907b2592652938bae`.
  Final source SHA is intentionally pending the Auth lane's approved base SHA.
- Hold #239 until it is normally restacked on that exact Auth SHA, CI passes,
  and independent review is refreshed against the resulting exact head. Never
  force-push; preserve concurrent owner changes in a follow-up PR.
- Hold #241. There is no schema or migration in this release.
- Redis-to-Celery publication is at-least-once, not provably exactly-once: a
  broker may accept a task and still return an ambiguous error. Queue claims
  therefore use unique ownership and compare-delete cleanup, while lifecycle
  and alert guards absorb a possible duplicate.

## Release control and immutable artifacts

Use the canonical sequence:

`approved source SHA -> staging-specific immutable image/digest -> staging evidence -> explicit production promotion`

- Staging and production images may differ because frontend configuration is
  build-time, but both must be built from the same approved source SHA and both
  digests must be recorded.
- The release lead, not an agent, authorizes deployments and promotion.
- No feature flag or data backfill applies. Recovery is image rollback plus
  safe reconciliation of in-flight bot and durable-inbox state.

## Preconditions and credential checklist

- [ ] Exact Auth base SHA supplied; final #239/follow-up head and merge base
  recorded; no overlap with Auth/IAM files.
- [ ] Staging data and every connected test account are attested synthetic-only;
  otherwise stop before deployment or provider traffic.
- [ ] Dedicated staging Zoom Marketplace app, with staging client ID, client
  secret, webhook secret token, OAuth redirect URI, and event endpoint URL.
- [ ] A provider-originated signed callback reaches the staging endpoint. The
  self-signed ingress probe proves only TLS/route acceptance, durable-inbox
  acceptance, and local signer/verifier consistency. It cannot prove the
  Marketplace secret matches or that best-effort queue publication succeeded.
- [ ] Synthetic staging Clerk provider and a separate wrong-tenant/view-as
  actor; session persistence can be observed without exposing cookies/tokens.
- [ ] Synthetic appointment is exactly bound to the intended provider,
  practice, patient, meeting occurrence, and unexpired capture authorization.
- [ ] Staging-only Zoom host/guest accounts and meeting; no pilot or patient
  account, recording, name, or clinical content.
- [ ] Staging Recall credential and dashboard access; Sentry issue access and
  confirmed human notification route.
- [ ] Founder resolves the current telemetry hold: Sentry intentionally
  initializes only in production, so staging issue delivery cannot be claimed
  until an approved staging sink/configuration exists.
- [ ] Read-only health access for API, worker, scheduler, queues, Redis key
  presence, database binding, and encrypted durable-inbox status.
- [ ] Previous known-good staging digest and rollback authority recorded.

Do not print credential values. Failure of any prerequisite is a hold, not a
reason to borrow production credentials or change provider configuration.

## Ordered rollout and evidence

1. Freeze the approved source SHA. Run focused Zoom/Recall tests, the full
   relevant backend suite, change-budget check, static checks, and independent
   review after the final substantive commit.
2. Build the staging-specific immutable image from that SHA and record the
   image digest. The release lead deploys it to staging and confirms API,
   worker, scheduler, queues, Redis, database, and durable inbox are healthy.
3. Sign in as the synthetic provider through Clerk, reload/reopen the app, and
   prove session persistence. Prove the expected practice and tenant. Attempt
   the same resource as the wrong tenant and under view-as; both must deny.
4. Connect the staging Zoom account through OAuth. Confirm only metadata that
   identifies the staging app/redirect, never credential material.
5. Start the synthetic Zoom occurrence and retain sanitized provider delivery
   metadata proving at least one provider-originated signed webhook was
   accepted by staging.
6. Confirm the occurrence resolves to exactly one pre-bound synthetic
   appointment and consumes its one-use authorization before bot creation.
7. Admit one synthetic guest. Confirm exactly one Recall bot is created,
   joins, records, and leaves. Confirm native consent and chat disclosure are
   visible before recording.
8. Speak only a fixed synthetic phrase. Confirm exactly one recording and
   transcript attach to the bound appointment and no other appointment.
9. Redeliver duplicate and reordered sanitized callback fixtures. Counts,
   binding, bot creation, transcript, and lifecycle state must remain
   idempotent.
10. Revoke staging OAuth/access, start a later synthetic occurrence, and prove
    no bot is created. Restore access only through the approved staging path.
11. Force one synthetic no-join. Confirm the expected reason-coded,
    PHI-scrubbed Sentry issue and human notification, then verify a later
    watchdog publication can re-arm after a simulated broker failure.
12. Observe for 30 minutes. Record healthy API/worker/scheduler status, no
    stuck queue work, no unexpected retries, and no unresolved durable-inbox
    intent. The release lead then decides whether the outside-party gate opens.

Evidence records: approved source SHA, staging image digest, timestamps,
scenario result, sanitized event/issue type, expected row/key counts, and
review/CI URLs. Store no payload body or raw identifier.

## Abort and recovery

Abort immediately for a source/digest mismatch, failed CI or review, provider
callback not proven, any tenant/view-as bypass, missing/late disclosure,
multiple bots or recordings, wrong appointment attachment, successful capture
after revocation, absent human notification, PHI-bearing telemetry, unhealthy
workload/queue, or unresolved durable teardown intent.

- Stop the synthetic meeting and prevent another occurrence from starting.
- Do not delete Redis ownership keys or encrypted inbox rows by hand; inspect
  pseudonymous references and let the reconciler establish bot teardown.
- The release lead rolls staging back to the recorded known-good digest. There
  is no schema rollback or data transformation in #239.
- Preserve sanitized failure evidence and open a narrowly scoped follow-up.
  Do not promote production until the failed rung is corrected and rerun.

## Outside-party meeting gate

The release lead and founder receive: exact SHA/digests, completed synthetic
evidence matrix, credential checklist, abort/recovery ownership, and this
meeting script: sign in; confirm tenant; connect Zoom; verify consent/chat
disclosure; start one pre-bound synthetic visit; admit one guest; speak the
fixed phrase; confirm one bot/recording/transcript and exact attachment; end
the meeting; confirm bot departure and workload health.

An agent does not contact or schedule the outside party. Pilot communication
and any production promotion remain explicit founder/release-lead decisions.

## Completion

- Final exact-head CI and independent review pass after the Auth restack.
- Every staging evidence item passes with no PHI and all abort criteria remain
  false through the observation window.
- Code, evidence, credentials checklist, and meeting script are ready at the
  outside-party gate; no production action has occurred.
