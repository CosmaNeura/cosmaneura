# CosmaBot current-encounter Session Prep briefs

Tier 2: an additive workload authorization boundary and clinical read contract.
The owner approved implementation, local review/checks and merge on September
29. Core deployment remains separate from CosmaBot's staging maintenance grant.
No schema, dependency, Scribe implementation or deployed configuration changes.

CosmaBot needs the cited brief for the clinician's selected encounter. The
provider's generic tool endpoint also exposes other tools; granting CosmaBot
that endpoint would exceed this scope. Add the dedicated
`POST /service/v1/companion/session-prep/brief` endpoint instead.

The receiver requires both `AGENT_API_ENABLED` and the new default-false
`COMPANION_SESSION_PREP_ENABLED`. Its sole allowed workload is the existing,
explicit `AGENT_API_COMPANION_CLIENT_ID`, using the same Entra tenant/audience,
app-only token, `AgentApi.Access` role and required `nbf` claim. Missing companion
identity denies access. It never aliases the Scribe or Sovereign identities.
The existing generic-tool and capture allowlists are unchanged.

Only `session-prep.context.read`, version `1.0.0`, `subject_ref=null`,
`purpose=session_prep` and `detail=brief` are accepted. The existing singleton
mount-context, interaction, client-turn and idempotency headers are required.
The closed JSON body is limited to 8 KiB; query selectors, duplicate keys,
other tools, raw/supporting detail and arbitrary patients are rejected.

Execution reuses the existing provider-only brief lifecycle: current clinician,
organization and encounter authority; the accepted `session-prep.open` source
turn; reviewed-note eligibility; bounded cited receipts; pending/ready states;
source-version checks and revocation on retry; commit before egress. It cannot
fall back to the process-local synthetic demonstration. The receiver is hidden
from the browser-generated API schema. Errors suppress submitted values, and
successful/error responses carry `no-store`.

## Validation

Run the two `tests/authority_kernel/companion_session_prep_*_test.py` files with
`--confcutdir=tests/authority_kernel`, alongside existing workload boundaries,
companion capture identity, Agent API authentication, Session Prep receiver and
server registration checks. This excludes root hooks that contact Clerk or
reset the shared test database. The flow test requires an explicitly owned
PostgreSQL URL for `companion_synth@127.0.0.1:55460/companion_prep_test`; external
networking is denied during the local run. It creates model tables in that
synthetic database, not a migration rehearsal. JWT decoding/model output are
synthetic; current authority, source review, queue and receipt operations run
through actual Core code and PostgreSQL. Run Ruff and Pyright for changed code.
Record the final tested/reviewed commit, commands and findings in the PR.

## Receiver-first rollout and recovery

1. Merge after passing local checks and implementer review under the owner's
   September 25 merge policy. No GitHub Actions or independent review gate.
2. Under a separately authorized Core staging rollout, inventory the actual
   deployed image/configuration and preserve ongoing Scribe/Session Prep work.
   Deploy the merged receiver with its new permission false. Verify service
   health and denied access before permission activation.
3. Verify Core's existing companion identity, tenant/audience and provider
   cohort. Then explicitly enable only this companion brief permission. Do
   not add the companion identity to `AGENT_API_SOVEREIGN_CLIENT_IDS`.
4. CosmaBot selects the new receiver and explicitly configures its own existing
   identity and audience. Keep its feature disabled until receiver readiness
   and scope are confirmed. There is no fallback to the generic tools route.
5. Use reviewed, finalized synthetic notes to check a cited brief on Mac and
   iPhone, pending completion, retry and access loss. Saved draft-note intake
   reads do not establish this acceptance. Real-service acceptance remains open.

For operational disablement, withdraw the companion permission and disable its
CosmaBot feature. For a code regression, restore the prior reviewed Core image;
the permission flag alone is not rollback of the shared handler refactor.
Retain existing briefs, receipts and review evidence; there is no data rollback.
