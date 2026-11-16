# Session Prep production activation and provider discovery

Risk: Tier 2. This checkpoint prepares a default-off Core configuration seam
and a PHI-free provider discovery read model. It does not deploy, migrate,
enable production, alter Sovereign, or rebuild the frontend.

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
canonical Agent API audience UUID, the derived production Clerk issuer, and an
existing record-grant signing secret. The issuer must be one canonical HTTPS
DNS origin and the signing secret must be an exact canonical 44-character
Fernet key; malformed variants fail the plan without emitting either value.
The retired `SESSION_PREP_PROVIDER_CLERK_SUBJECTS` key remains prohibited in
generic production environment inputs so an allowlist cannot be reintroduced
outside this contract. The staging-only Session Prep alert transport is
deliberately absent.

`GET /internal/v1/settings` adds `session_prep.eligible` and
`session_prep.can_review_sources`. The response contains no provider subject,
account allowlist, chart data, or authorization token. It reports only the
current doctor context selected by the existing authenticated dependency chain.
Both booleans remain false during support view-as. When the global switch is on,
every active authenticated account qualifies automatically without enrollment.
Deleted accounts and effective cancellation remain unavailable. All review,
exchange, and read endpoints continue
to enforce their existing live record and note ownership checks, including the
provider-own-visit boundary; this response is not an execution grant.

The note-detail UI consumes both discovery booleans in addition to the
build-time Sovereign integration flag. It renders neither the Session Prep
panel nor prior-visit controls until Core says the current provider is eligible
and can currently review sources. Missing or older cached discovery state fails
closed; the next successful settings read exposes the feature automatically
after an active existing or new account refreshes its settings.

## Read-only preflight

Before any activation proposal:

1. Pin exact reviewed Core and provider Sovereign commits and immutable image
   digests. Verify the provider panel asset/SRI was built from that same
   Sovereign source and with production authority enabled.
2. Run `tofu -chdir=infra validate -no-color`, then produce a saved **plan only**
   with the proposed `session_prep_production_rollout`. Do not apply it. The
   plan must show the same global Session Prep switch on API, worker, and
   scheduler; no provider-subject allowlist; the complete non-secret Authority
   Kernel runtime on API and worker; Agent API identity only on API; and no
   unrelated resource change.
3. From the exact candidate source, run
   `PYTHON_ENV=production uv run python scripts/session_prep_production_eligibility_preflight.py`
   with the production application's proposed `DATABASE_URL` supplied privately.
   It must name the exact production endpoint and use `sslmode=verify-full`
   with `/etc/ssl/certs/ca-certificates.crt`; `prefer`, `require`, `verify-ca`,
   a missing or alternate trust root, and extra connection options are refused.
   A successful connection therefore validates the server certificate chain and
   hostname through libpq. The command also requires the current backend to
   report an encrypted TLS 1.2 or 1.3 session before any inventory query.
   The command imports no application code, opens one repeatable-read read-only
   transaction, and emits only fixed denial-reason counts, eligible/total,
   observation time, rollout state, the sole observed Alembic head, and a digest.
   Review that count-only output. It refuses unknown or ambiguous access state,
   more than one database head, a database head different from the candidate's
   sole repository head, or missing ancestry through `443f26a5dfe7`,
   `c80db51e6a75`, and `de9260834611`. It never enables Session Prep or emits
   provider, practice, account, or chart identifiers. Any migration application
   is a separate founder-approved operation with a rehearsed forward-recovery
   plan.
   The currently observed stored `sslmode=prefer` URL remains a blocking
   configuration mismatch; this source checkpoint does not alter or approve it.
4. Verify the dedicated production provider Sovereign identity has only the
   required Agent API app role, its configured client ID equals the proposed
   Core allowlist, and its Core audience/issuer pair is exact. Verify the public
   Sovereign app/runtime is absent. A retained public foundation identity may
   exist, but it must have no Agent API app-role assignment.
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
existing or future active authenticated account for its own records. No
onboarding, practice enrollment or feature-specific user list is required.

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
recovery evidence are prepared. Keep `AUTO_MIGRATE_ON_STARTUP=false` and apply
`ddd6261fa36f` before starting any new ORM runtime; deploy compatible Core
worker/scheduler readers and drain old revisions before API. Deploy the matching
provider/frontend pair, verify it, then enable `session_prep_production_rollout.enabled`.
Existing and new active accounts then qualify automatically for their own visits.

Use `.github/workflows/build_and_publish_local.yml` with `release_action=prepare`,
then `release_action=promote`. Both dispatches include
`github_hosted_fallback_authorized=true`, the applicable `terraform_apply_run_id`,
`provider_signed_recall_callback_verified=true`,
`production_session_prep_frontend_enabled=true`, and
`production_session_prep_reviewed_core_sha` equal to the final reviewed master SHA.
Promotion also supplies that successful `producer_run_id`, `producer_run_attempt`
and `candidate_artifact_id`, without rebuilding. The one-time
`production_release_verified.yml` is not this release path.

Setting `session_prep_production_rollout.enabled=false` also disables the shared
Authority Kernel and Agent API gates; it is not a Prep-only stop and can interrupt
Scribe receiver flows that use those gates. Preserve the receiver and shared
configuration during image recovery. Recover only account-namespace-compatible
Core images and matching Sovereign assets; retain additive schema, ownership and
audit history. Never lower correction frontiers, delete records or assume an
automatic old-image rollback.

## Current automatic authorization

One resolver admits every active persisted Doctor account without enrollment.
Deleted accounts and effective cancellation remain unavailable. Record
ownership, actor/identity/tenant revocation and view-as are independent checks.
The retired `SESSION_PREP_PROVIDER_CLERK_SUBJECTS` input grants no access.

## Shared provider exchange and Scribe continuity

The provider token is shared infrastructure, not a Session Prep grant. Browser and
trusted-native capture retain their existing product decision, exact owner,
view-as refusal and fresh authorization; a valid legacy pilot can retain only
that capture access. Optional Prep provisioning runs in a savepoint, and only
policy-specific unavailability may fall back to capture, without a Prep
capability or context. All lineage, identity, membership and offboarding
conflicts still deny; Prep workers and reads retain those ownership fences.
Recovery preserves Scribe receiver/shared configuration and retains
account-namespace-compatible readers and schema.
