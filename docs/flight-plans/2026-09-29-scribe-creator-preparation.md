# Use atomic creator verification for recording preparation

Owner: MCD operational beta owner. Tier 2: approved capture-start performance
work. Requires the Scribe creator-recording-window receiver to be deployed first.

For configured issue-lease, renew-lease and ordinary chunk-reservation operations,
send one request to the additive `/v1/captured-sessions` preparation routes. These
routes run the same live creator/consent/retention verification inside the existing
mutation transaction before a write or exact receipt replay. This replaces the
separate creator-review HTTP request; it must not be removed without that receiver.

Current Core admission remains checked before and after I/O. Tenant/session/creator
come from the admitted binding. Keep the original operation, idempotency-key hash,
request payload, receipt validation, and error/cancellation behavior. No token or
authority cache is introduced. No microphone permission is issued by preparation.
Legacy sessions, explicit profile reservations, encryption keys and all other
operations retain their current routes and separate status/creator checks.

The workflow client accepts only the three typed operations for this new route,
validates the creator header, and rejects profile overrides. Missing/unavailable
routes, denied access, uncertain responses and malformed receipts do not select
legacy fallback or an automatic retry. Explicit recovery keeps the original key
and body. Source-start decisions remain a separate fresh operation.

Qualification covers actual gateway/client HTTP composition with synthetic
authority/provider faults: one request rather than two, current access boundaries,
changed admission, original keys and payloads, response loss, malformed replies,
and preservation of legacy/global-key behavior. The Scribe receiver additionally
requires real PostgreSQL concurrency, consent, creator and restart/replay tests.
This is not physical startup qualification.

Release the merged Scribe API first, verifying exact deployed source, active cohort
and old/new route boundaries; then deploy this merged Core sender while retaining
unrelated Session Prep, CosmaBot, service-auth and deployment configuration. The
immutable staging images control the existing synthetic cohort until its next
replacement. No native update, migration, dependency or environment change is
needed. Reconcile all affected images/configuration before applying either.

Recovery: restore the prior Core sender image first. Additive Scribe routes may
remain. Preserve existing recordings, keys, journals and all receipts. Production
or cohort expansion remain separate owner decisions. Measure current native
Record/Resume-to-audio latency before claiming the four-second target.
