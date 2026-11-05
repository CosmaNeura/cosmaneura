# Staging Scribe preserving initialization executor

Tier 2. This dependent source slice connects the pure initialization contract to
the existing founder-local ReleaseV2 deployer. No workflow dispatch input, app
dependency, cloud service, schema change or automatic activation is added.

## Permission and immutable source

`AGENTS.md` and `CLAUDE.md` still designate founder-manual **Deploy Staging
Candidate** as the only authorized staging path. The optional local bridge is
technical support. Using it requires an explicit owner exception to that policy
and approval of the exact source, published image, migration, controls, cohort,
private-state proof and closed recovery. Actions stays disabled.

Merge/review the pure contract and this executor in dependency order. Build a
fresh immutable desired-runtime candidate through the existing source-review,
founder, identity, provenance, material, rehearsal and receipt gates. An earlier
dark artifact's release object cannot be rewritten for a different runtime.
After publication fixes the image digest, prepare and hash-pin a separate
initialization plan with `prepare_plan` from the pure contract. This avoids a
circular image-digest build input and never changes build/publication history.

The plan binds candidate/controller, selected PR, founder, identity hash,
schema before/after, image, finite expiry, full secret-inclusive raw before and
desired hashes, component hashes, exact revision names, complete 13 API controls
and same-source/image closed recovery. Its `recovery.compatibility_sha256` must
point to actual owner-verified source qualification. The pure contract validates
its hash and binding; it does not invent or run that qualification. The existing
build controller separately validates the actual migration rehearsal receipt.

Retain the separate staging receiver and selected staging capture pipeline.
The owner-selected initial cohort is only the existing synthetic practice;
receiver/native identity remains disjoint from the tool caller. This executor
changes no billing exemption, membership, account, personal phone content or
production setting. Runtime controls require owner, finite expiry and exact
initial cohort; expiry does not implement automatic runtime withdrawal.

## Optional invocation inputs

All existing founder-local ReleaseV2 inputs remain required: bound build plan
and publication receipt, schema rungs, retained recovery digest and identity
fingerprint. `STAGING_PRESERVE_EFFECTIVE_CONFIGURATION` must be true.
After approval, also set:

- `STAGING_SCRIBE_INIT_PLAN`: owned canonical absolute `/private/tmp/` plan path.
- `EXPECTED_STAGING_SCRIBE_INIT_PLAN_SHA256`: raw-file 64-hex SHA-256.
- `STAGING_SCRIBE_INIT_JOURNAL`: canonical owned `/private/tmp/` journal path in
  an owner-only directory, absent for first initialization.
- `STAGING_SCRIBE_INIT_ACTION`: `initialize` (default) or `closed` for recovery.
- `EXPECTED_STAGING_EFFECTIVE_CONFIGURATION_SHA256`: the pure plan's complete
  canonical `desired_sha256`, replacing the legacy subset preserve hash only
  while the explicit bridge is present.

Invoke `infra-staging/deploy.sh` with the exact candidate SHA/published digest
through the reviewed local controller after the owner exception. The initializer
refuses service-principal mode, partial inputs, preserve=false or missing
publication. Desired revision names must match the deployer's run-bound suffix,
retaining its existing old-worker drain proof. Hosted workflow remains unchanged.

## State preservation and phases

The observer uses only fixed three-app staging management/revision reads and
the documented `containerapp secret list --show-values`. Private values stay in
memory/stdin, never argv, logs, journal or artifacts. The journal stores public
receipt rows, hashes and exact mutation intent. Protected resolver inputs used
by the schema job must match currently resolved private references exactly.

Hash every app's complete raw template, configuration, identity and private
values. Preserve unowned env presence/order/value, secret refs, billing refs,
command absent/null/list, args, imageType, probes, resources, ingress and scale.
The current worker health flag is true; API/scheduler omit it. Build-runtime
comparison uses existing per-workload scope and cannot normalize those omissions.

Only immutable image, exact revision suffix and `--set-env-vars` are allowed:
worker ledger=true, scheduler ledger=true, then exactly 13 API public controls.
No secrets set/remove, env removal, args/command, billing or other-resource
updates exist in this grammar. Every phase checks complete target raw state and
all untouched apps. Mock tests do not prove real provider shape normalization;
any provider target mismatch stops with journal retained for owner disposition.

Initialize only after durable raw before receipt, then run the existing schema
expand/post-schema gates. Worker fence and old-worker drain precede scheduler
fence; API intake opens only after both. Source/identity/receipt gates remain.

## Failure and closed recovery

Record exact durable intent before each mutation, under a stable owner-only
lock with atomic fsynced journal replacement. Foreign plans, malformed or swapped
receipts, unrecorded target revisions, private/raw drift, wrong schema and
expired plans refuse. An uncertain initialization request is never resubmitted,
and restarting cannot create a fresh before receipt over a partial state.

Owner-approved `closed` recovery consumes the same plan/journal and probes the
already-expanded schema. It does not apply or downgrade a migration. It updates
worker, scheduler, then API to the qualified same-source/image closed state:
ledger true everywhere; native status/read/export context available; visits,
actions, automatic activation and cancellation intake false; cohort refs empty.
Existing receipts, revocation fences and retention remain effective.

Only recorded baseline/desired phases are accepted. If an uncertain CLOSED
request already produced its exact recorded healthy target, read-only
acknowledgement records completion without another request. Otherwise stop for
owner disposition. Final closure verifies the aggregate raw hash. Legacy raw
revision recovery also refuses dropping any enabled ledger fence or reopening
acquisition. Do not use raw rollback, additive-schema removal or consent
revocation to erase fences. Same-image configuration recovery cannot fix a code
regression; keep a qualified forward image available.

This slice does not add a remote processing fence; accepted remote transcription
may finish. Preserve authorized read/export and truthful cancellation wording.
The owner must close/reconcile the cohort at expiry. Recovery still requires a
current permitted source/build envelope and the plan's approved window; renew
truthfully if expired, without altering published history or clamping time.

## Validation and remaining release gates

Run fresh pure/mocked helper and extracted real shell tests with Core Python
3.13 and root conftest/plugin autoload cut off. Check phase ordering, unknown
responses, exact-target acknowledgement without duplicate update, source and
private/raw/journal drift, true worker health with absent API/scheduler flags,
fixed command grammar and schema-probe-only closure. Existing CLI, ReleaseV2,
source/controller and canonical shell tests qualify prior paths. Regenerate
the protected dependency inventory from actual tracked blob/mode identities,
including both helpers, without changing size thresholds or protections.

Source preparation/tests do not authorize publication, migration, provider
mutation, receiver promotion or physical-device qualification. Actual provider
full-state compatibility, closed-source qualification, final owner execution
approval and local-path policy exception remain release gates.
