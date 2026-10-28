# Recall recording response correspondence — 2026-10-25

Tier 2: external recording identity and encounter correspondence. Owner: the
current virtual-lab integration task. This change does not activate a cohort.

## Problem and contract basis

The official Recall OpenAPI specification defines a successful
`POST /api/v1/bot/{id}/start_recording/` response as `Bot`; its top-level `id`
is the bot ID. The actual recording ID belongs to `recordings[]`. The prior
adapter returned the response unchanged, so the caller used the bot ID in its
recording-scoped doctor/appointment/patient keys. Existing tests substituted a
Recording-shaped response and missed this mismatch.

Source: https://docs.recall.ai/openapi/meeting-apischema_latest.yml
Fetched 2026-10-25; SHA-256
`6f17b649e4d017dadd76f258d2267b36447294097ab20267249ed4fa7619a6de`.
The owned evidence packet `core-recall-recording-20261025` retains the official
schema, the exact synthetic response, and a comparison through the unchanged
caller using the deployed 9666 adapter and the candidate adapter. This is a
reproduced contract defect, not a claim that a live lab bot has run.

## Change and limits

The adapter snapshots existing recording IDs before the single Start POST,
requires the same bot identity, and resolves exactly one new recording. It
waits for a non-null start, an explicitly null completion, and current
`in_call_recording`. A prior recording, multiple new candidates, malformed
identity/timing, a terminal bot or an uncertain provider response cannot be
adopted. The return contract stays `{"id": actual_recording_id}`.

Post-start confirmation has a 15-second deadline. Each read is capped at the
remaining deadline or five seconds, whichever is shorter; asyncio timeout
covers headers and the entire streamed body, capped at one MiB. No redirect
or provider mutation is used by these reads. There is no second Start POST.

Unresolved starts raise and retain the consumed occurrence authorization and
known bot/release obligation. A known bot is first published with its original
Redis lifecycle token; a failed publication still attempts the original exact
release intent before raising. An intent-store failure retains a successfully
published bot for normal meeting-end cleanup. No second Start or new bot is
attempted; ambiguous Create without a returned bot ID remains unconfirmed.

Both launch entry points pass the original acquired lifecycle lock. Its token,
TTL, occurrence, doctor and expected bot define the shared deadline. The
existing lifecycle180 and teardown180 seconds remain unchanged. Before Create,
a launch needs125 seconds for bounded Create30, full consent readiness45,
pre-read5, Start30 and confirmation15, plus a five-second publication reserve.
Slow join-URL lookup or authorization work can make launch fail before Create;
they cannot grant a fresh budget. Every Recall request has a whole-response
limit. No timed-out background thread can continue a POST.

Token/occurrence/doctor/bot-checked Redis scripts publish the recording's three
owner keys and capture flags atomically. Final same-appointment, same-occurrence,
non-ended meeting status and pending/retry-wait launch-intent retirement share
one PostgreSQL transaction. Claimed or already completed intents are refused;
row waits have one-second limits, with original-token checks before commit.
A concurrent committed end cannot be changed back to started. Disclosure chat
runs after ownership publication with a five-second ceiling. It is best effort;
Zoom's normal recording permission remains the primary gate.

## Validation and promotion

Focused tests cover the official Bot shape with distinct IDs, delayed
recording creation/current status, completed old recordings, ambiguous or
foreign candidates, existing capture, POST timeout, one shared polling
budget, slow headers/body, size bounds and caller teardown retention. The
normal six-hour retention and manual recording configuration remain covered.
Additional tests cover original-token replacement, expiry after preflight,
known-bot preservation on database failure, full consent timing, whole-response
Create/Start cancellation, pending-intent transaction rollback and a concurrent
PostgreSQL meeting-end row lock. Existing Zoom webhook/start/cleanup regressions
run in an isolated table clone; this is not a migration rehearsal.
Tests use synthetic provider responses, real task-owned Redis and isolated
PostgreSQL schemas; they make no real audio, provider API or account changes. The isolated runner excludes global Clerk cleanup and
database reset hooks; its retained first plugin-configuration failure does
not count as passing evidence.

Before promotion: independent review after the final substantive push,
current-master reconciliation of web/worker/scheduler images and configuration,
and a reviewed immutable merged-source build. Preserve the current note engine,
Session Prep, schemas, migrations and release cohorts. No deployment is part
of this PR. Actual normal OAuth, authorized occurrence, current recording,
consent, source playback, stop, transcription and dashboard evidence remain
required for bot acceptance.

## Recovery

On an uncertain launch, retain the original intent and exact bot/occurrence
binding; do not call Start again or guess a recording ID. Use the existing
reviewed teardown reconciliation and inspect only the owned occurrence.
For a source regression, stop further synthetic trials and prepare a reviewed
corrective commit on current merged trunk, then rebuild against the current
staging state. Do not roll back to an old image across unrelated drift, delete
ownership keys, clear consumed authorization or alter existing recordings.
