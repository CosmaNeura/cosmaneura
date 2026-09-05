# Flight plan: dormant staging runtime-record builder

## Outcome and risk

- Tier **2** adds a pure, dormant builder for the unsigned external runtime
  record. It consumes only a sealed configured-policy snapshot, an
  authenticated GitHub publisher invocation, and an authenticated immutable
  image publication.
- The builder derives the canonical record, its full content-derived
  `runtime-<64 lowercase hex>` ID, SHA-256 digest, and exact private Blob URI.
  It performs no filesystem, environment, clock, subprocess, credential,
  network, signing, publication, workflow, or Azure operation.
- The receiver and builder share the same closed-world content validator, so a
  record cannot use a stale, shortened, caller-selected, or field-incomplete
  identifier.

## Authority and ordering

- The build-policy seal snapshots canonical configured policy bytes and their
  activation-independent binding digest. It requires every exact pin but does
  not treat false activation flags as permission to execute a restore.
- The publisher-invocation seal supplies the exact repository, protected ref,
  signer identity, `workflow_dispatch` event, protected-ref assertion, source
  SHA, canonical run/attempt numbers, and UTC-second record creation time.
- The image-publication seal supplies the exact registry repository, resolved
  lowercase SHA-256 image digest, image-build source SHA, and UTC-second
  publication time. The builder refuses unless the image-build source SHA is
  the publisher source SHA and record creation is strictly after publication.
- Callers cannot supply the record ID, record URI, image references, policy
  fields, timestamps, source SHA, or run URI independently. The record ID is
  SHA-256 over canonical JSON containing all 21 other required record fields;
  the final 22-field record is then canonicalized and content-addressed.
- Python seals prevent accidental API misuse, not hostile same-process
  forgery. Future adapters that mint either authority require their own
  independent review and authenticated evidence.

## Hold, recovery, and remaining gates

- Hold after focused and broad offline tests, Ruff, Pyright, compile, policy
  hash/pin validation, change-budget verification, independent final-head
  review, and protected PR checks.
- Recovery is a code-only revert while dormant. No artifact, Blob, image,
  credential, schema, data, infrastructure, or live-state mutation occurs.
- All activation flags remain false. This slice does not authenticate live ACR
  provenance, implement the publisher execution environment, sign or upload
  records, prove create-only Blob behavior, attach pointers to jobs, integrate
  a workflow, authorize rehearsal, or permit staging/production promotion.
