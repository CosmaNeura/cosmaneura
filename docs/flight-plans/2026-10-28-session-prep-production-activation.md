# Session Prep production activation and provider discovery

Risk: Tier 2. This checkpoint prepares a default-off Core configuration seam
and a PHI-free provider discovery read model. It does not deploy, migrate,
enable production, select a provider, alter Sovereign, or rebuild the frontend.

## Source checkpoint

`session_prep_production_rollout` is the only Terraform input allowed to project
the Core production receiver gates. Its default is disabled with no paired
Entra identifiers. When a later reviewed plan explicitly
enables it, the same boolean supplies `AUTHORITY_KERNEL_ENABLED`,
`AGENT_API_ENABLED`, and `SESSION_PREP_PROVIDER_ENABLED`; Core cannot plan one
of those three gates on while leaving another off. That global availability
state is projected identically into API, worker, and scheduler; there is no
per-user Session Prep allowlist. Agent API identity settings remain API-only.
The non-secret Authority Kernel enabled
state and token lifetimes are identical on API and worker because the worker
revalidates current authority before and after generation. They are not sent to
the scheduler, and no Agent API identity or record-grant secret reaches the
worker.

Activation requires one nonzero canonical Sovereign client UUID, one nonzero
canonical Agent API audience UUID, the derived
production Clerk issuer, and an existing record-grant signing secret. The
issuer must be one canonical HTTPS DNS origin and the signing secret must be an
exact canonical 44-character Fernet key; malformed variants fail the plan
without emitting either value. The staging-only Session Prep alert transport
is deliberately absent.

`GET /internal/v1/settings` adds `session_prep.eligible` and
`session_prep.can_review_sources`. The response contains no provider subject,
account list, chart data, or authorization token. It reports only the
current doctor context selected by the existing authenticated dependency chain.
View-as may discover that the target is selected but can never advertise source
review authority. Source review is advertised only while the same current
provider/practice/BAA/entitlement decision used by the review route is in
force. All review, exchange, and read endpoints continue to enforce their
existing live record and note ownership checks; this response is not an
execution grant.

The note-detail UI consumes both discovery booleans in addition to the
build-time Sovereign integration flag. It renders neither the Session Prep
panel nor prior-visit controls until Core says the current provider is eligible
and can currently review sources. Missing or older cached discovery state fails
closed and refreshes through the ordinary settings query.

## Read-only preflight

Before any activation proposal:

1. Pin exact reviewed Core and provider Sovereign commits and immutable image
   digests. Verify the provider panel asset/SRI was built from that same
   Sovereign source and with production authority enabled.
2. Run `tofu -chdir=infra validate -no-color`, then produce a saved **plan only**
   with the proposed `session_prep_production_rollout`. Do not apply it. The
   plan must show the same Session Prep enabled state on API, worker, and scheduler;
   the complete non-secret Authority Kernel runtime on API and worker; Agent
   API identity only on API; and no unrelated resource change.
3. Read production metadata without chart content. Require a sole Alembic head
   compatible with the candidate and ancestry through `443f26a5dfe7`,
   `c80db51e6a75`, and `de9260834611`. Any migration application is a separate
   founder-approved operation with a rehearsed forward-recovery plan.
4. Verify the dedicated production provider Sovereign identity has only the
   required Agent API app role, its configured client ID equals the proposed
   Core allowlist, and its Core audience/issuer pair is exact. Verify the public
   Sovereign identity is absent.
5. Compare current API, worker, and scheduler image digests, environment-name
   sets, readiness, and retained recovery revisions. Refuse mixed Core images,
   duplicate environment keys, missing recovery, or stale frontend assets.
6. Run synthetic, provider-owned acceptance against the candidate environment:
   prior reviewed visits, current-visit exclusion, correction/re-review,
   fresh/stale brief behavior, export/persistence, foreign-owner denial, and
   worker restart/retry. No production chart is part of preflight.

## Founder-only activation inputs

The founder must still choose and authorize all of the following in one release
record. Enabling the global switch makes Session Prep available to every
existing or future provider whose current persisted authorization satisfies the
strict provider/practice/BAA/entitlement predicate; email domain or a legacy
pilot grant alone never qualifies an account.

- aggregate-only production eligibility evidence, including zero integrity
  violations and an acknowledged eligible-provider count;
- exact Core and Sovereign source SHAs and immutable image digests;
- production Sovereign managed-identity client ID, Agent API audience, and
  confirmed app-role assignment;
- provider frontend build with
  `VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED=true` and the exact production
  Sovereign origin;
- schema/migration operation, downtime window, workload order, and retained
  recovery revisions;
- alert destination and production PHI-free worker health design (the current
  Session Prep alert client is staging-only);
- final enable/apply authorization after the saved plan and acceptance evidence
  are reviewed.

## Activation and recovery order

Keep the rollout object disabled while images, schema, identity, frontend, and
recovery evidence are prepared. For an approved cutover, apply schema first if
needed; update/drain Core worker and scheduler before Core API; deploy the paired
Sovereign service and frontend only under the same release record; then run the
provider-owned synthetic acceptance. Existing and new providers become eligible
automatically as their current account, practice, BAA, and entitlement records
enter the approved states; no feature-specific user-list update is required.

Fast stop is a reviewed plan setting `session_prep_production_rollout.enabled`
back to `false`, which closes new review, exchange, worker, and real-record read
paths together while retaining additive schema and audit/receipt history.
Recover application images only to a migration-compatible retained Core and
Sovereign pair. Never lower correction frontiers or delete Session Prep records
as rollback.

## Current automatic authorization

One resolver combines Doctor lifecycle, active seat-holding practice membership,
organization-roster or workforce-attestation authority, an in-force BAA, and
ordinary current practice entitlement. Discovery and every provider-data
execution boundary revalidate that decision; record ownership and view-as
restrictions remain additional independent checks. A revoked legacy pilot grant
is a denial tombstone unless a current active replacement exists, and a pilot
grant can never substitute for practice entitlement. The retired
`SESSION_PREP_PROVIDER_CLERK_SUBJECTS` variable is accepted only under a bounded
legacy grammar during staging recovery inspection and is removed during the
deployment; exact post-state evidence rejects it.
