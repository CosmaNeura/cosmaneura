# Session Prep schedule recovery

Risk: **Tier 2**. This change touches a clinical provider route and its protected
error rendering. It changes frontend recovery only.

## Observed failure and outcome

On 2026-10-29, staging's frontend recorded `Meeting not found` with status 404
after successful schedule responses. A stale meeting link reached the generic
React crash-reporting path. A separate event reported `schedule data failed to
load`; its upstream cause remains unproven.

Only a successful schedule list missing the requested meeting now produces a
deliberate 404 recovery screen with links to the schedule and visits. Failed
schedule reads produce a temporary 503 recovery with manual retry; a 401 remains
session verification. The existing 30-second read deadline bounds SDK, token
acquisition and parsing, aborts its request, and preserves navigation cancellation.
Retry is disabled while pending. A failed read never mounts meeting, patient,
note or capture controls, even if its response carries successful-looking data.

Existing practice-gate responses and billing redirects remain authoritative.
`ViewAsProtectedErrorGuard` verifies the current support-view state before
showing recovery, preserves the active read-only banner, and hides provider
content when verification is unavailable. No authorization rule, schema, data,
feature flag, backend, Scribe service, runtime configuration or production
activation changes. Recovery never starts or retries encounter capture.

## Integrated release and acceptance

The seven implementation/test paths were frozen at
`fe513c4a0173fd06206f962b468109442532e50f`, based on Core
`9f6f07d35ae185e4381946f6a59554ec8c26a8d3`. Publish and merge through the ordinary
reviewed PR path. Build the complete frontend from the exact merged Core source;
bind its immutable image and bundled asset manifest to the integrated Session
Prep release. Preserve the separately running Scribe and public Sovereign
workloads. This frontend change requires no migration or environment switch.

The 46 focused tests cover deliberate missing-meeting recovery, failed-list
classification, manual retry, cancellation, deadline expiry, session and
practice gates, billing redirects, and actual support-view guards. Full frontend
typecheck, route type generation, formatting and scoped lint also passed.

After the reviewed staging rollout, verify the deployed source/image and asset
hashes first. With synthetic provider-owned data, open the dashboard, schedule
index and a current meeting; then open an absent meeting link and return safely
to the schedule and notes. Verify that Session Prep still opens from an owned
visit. Exercise temporary/session failure in an isolated test context so the
current Scribe login stays intact. Recheck read-only support and unavailable
verification: neither may expose meeting or capture controls. Record live
acceptance separately from the existing local test evidence.

## Exact recovery

A reviewed normal revert of `fe513c4a0173fd06206f962b468109442532e50f` restores
the original seven implementation/test blobs from Core `9f6f07d3`; stop for
review if later edits cause conflicts. Rebuild and release the reverted source's
complete frontend and asset manifest through the same verified release path.
There is no data, schema or configuration rollback.

For an approved immediate image recovery, use the exact migration-compatible
pre-rollout Core web image and bundled asset manifest recorded in the release
receipt, after checking API/worker compatibility. The observed baseline is
`sha256:7e4926c2cdc65418f3f207367285eb0c5189d202c3ca1b8bba36369c39dd9c7e`
with declared and publicly verified frontend source
`30af0d7ac356cb24b734ff5a80ef707e6cb55690`. Its schedule asset is
`/assets/schedule-TjjxDRqk.js`, SHA-256
`6d538737945bb68e0ae1a0c4a3de5578a1289bd1e616ea77ca21b0b1652169ca`.
If the baseline changes before deployment, capture and review the replacement
receipt rather than using this historical image. Preserve current configuration,
schema, Scribe and Sovereign state; this plan authorizes no automatic rollback.
