# Session Prep bounded safety regeneration

Tier 2: provider-source model generation. This Core-only repair changes no
security boundary, source selection, cohort, schema, external contract or
dependency. Base is merged Core `64cefd7c0786d8ab5ebf3e007dd28cfde8940dee`.

## Observed problem and evidence limit

Staging's fictional corrective-history visit exhausted three build attempts
with fixed `output_safety_rejected` metadata under `source_only_prep_v2`.
Rejected model drafts were discarded, so their exact wording and the precise
live rejection cause are unknown. A controlled reproduction against the
unchanged validator demonstrates a relevant class: “the prior instruction to
increase losartan to 50 mg was entered in error and superseded” is rejected
despite describing a historical correction. A neutral description of the same
dated planned doses and supersession passes. This reproduction establishes a
wording conflict, not the contents of the discarded live draft.

## Resulting behavior and bounds

`source_only_prep_v3` asks for dated neutral descriptions of documented plans
and corrections while retaining critical source values, source references,
uncertainty, measurement provenance and order/result distinctions. If a
schema-valid, source-lineage-valid initial draft fails the existing safety
validator, exactly one fresh draft may be requested from the identical original
reviewed-source payload with more explicit neutral-wording instructions. The
rejected draft is never returned, persisted, logged, transformed or included in
the second model request. The second draft must pass the complete unchanged
schema, lineage and safety checks. An unsafe second draft fails closed.

No regeneration occurs for schema, lineage, source bounds, output bounds,
transport, rate-limit, timeout, empty or truncated-output failures. Existing
fixed failure codes and three worker attempts remain unchanged. One successful
initial draft costs one model call; an initial safety rejection costs at most
two calls in that worker attempt, at most six across three worker attempts.
Each call retains the 3,500 output-token cap and has zero SDK retries. No new
model, external service or credential is introduced.

Calls use a 25-second HTTP timeout, including a connect timeout of at most five
seconds, and share a 50-second monotonic budget. Remaining time limits the
second call; an expired budget prevents further calls or a successful return.
HTTP timeouts apply to transport operations rather than guaranteeing a total
wall-clock interrupt across arbitrary synchronous SDK work. The existing
Celery 60-second soft limit and 75-second hard limit are therefore retained as
task-level safeguards inside the unchanged 90-second publication lease. Source
authority, current-source rechecks and lease checks still run before artifact
publication; this repair does not extend them or persist a late draft.

The prompt version is already part of the source digest, so existing v2
artifacts/failures cease to match current preparation after rollout. Existing
refresh/build recovery produces a fresh v3 request without rewriting source
notes or historical artifacts. Web, worker and scheduler should use one merged
Core candidate; no Sovereign or Scribe change is required for this repair.

## Validation and release

Use only synthetic A/B/D-derived reviewed-note fixtures and the isolated local
PostgreSQL test database. Test the actual generator adapter with fake SDK
responses, fixed failure codes, original-payload identity, one-call success,
two-call rejection bounds, source-citation preservation, unchanged action/new
diagnosis/emergency-deferral rejection, shared timeout budget and late-output
discard. Worker tests must show that only the validated second draft persists,
changed sources suppress publication, and v3 changes the digest without changing
reviewed sources. These scripted responses establish control behavior; they
do not measure real-model clinical completeness or prove the discarded live
failure's exact cause.

Completed locally: 218 bounded offline tests across generator, runtime, safety,
reviewed sources, provider context, authority and existing receivers passed.
After replacing concrete prompt examples with source-value placeholders, the
68 overlapping generator/runtime/safety tests passed again. Changed Python
files pass Ruff and Pyright; the diff passes whitespace checks. External HTTP
was blocked and only the private synthetic PostgreSQL database was used.

After the final substantive push, an independent same-task agent must review
the exact remote commit and record findings/dispositions. Parent coordinates
merge and any later authorized staging release. A live synthetic corrective-
history check remains necessary to assess model wording and latency after
rollout; do not declare the staging failure resolved from fake-adapter tests.

Recovery restores the previous merged Core image consistently across web,
worker and scheduler. Its v2 source digest becomes current again; stored notes
and immutable artifacts are retained. No migration or destructive rollback is
needed. No production action is included.
