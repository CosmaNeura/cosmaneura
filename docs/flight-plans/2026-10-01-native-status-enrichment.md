# Native opening: omit unused saved-status queries

Tier 2: this is adjacent to patient-record authorization. No authorization,
session, schema, configuration, identity or public contract changes.

## Problem and bounded change

The native read facade invokes encounter lookup five times for mount and four
times for each read operation. Every lookup currently reads saved transcript and
note references even when its encounter projection is discarded. The measured
Mac opening sequence (mount, creator review, capabilities) therefore performs
26 status SELECTs. Only the final mount encounter is published.

Keep all 13 lookups and their full current provider, record, patient, binding,
ownership and available/deadline checks in the same order. Skip only the two
display-enrichment SELECTs on unpublished lookups. Final mount performs its own
enrichment, giving two status SELECTs for that sequence. No rows, credentials,
tokens, gateway decisions or old status values are cached. The default public
lookup, catalog batch enrichment, creation and DOB flows remain unchanged.
The internal keyword is absent from request models and cannot be supplied over
the native or encounter API.

## Source and deployed-state reconciliation

Base Core `9f5c61122934c1eb5a32868be9088945183fab37`; affected backend files match
Core30's deployed source `1b105a1798dc659be9f216c5ea8a24606ac7b354` and earlier
facade source `bc9da3478de0e03bacac0b81495ea43956c51279` byte for byte.
Core30 image is `sha256:cd95c060e41a079ecc61aa6b4d07711e1ec25e1cd0bb1a2e45d38fa98e3a6366`.
Receiver30 is already compatible: source
`ec19138614cf2975832765a528632d45ae8ec60d`, image
`sha256:e1d4f7b59aef6220f0e05e9c8c243fd55bececdf210e1f87bd97bfd26a0bc209`.
Preserve all unrelated deployed components and previously frozen native builds.
The output-only design freeze binds the original qualification/file hashes.

## Validation and limits

Use the existing marked synthetic local PostgreSQL fixture, isolated authority
conftest and minimal environment. Test the actual deadline router, facade,
encounter services, context exchange, admission and gateway with real SQL.
Verified Clerk/workload identities and external Scribe consent/session/read
receipts are explicit invented substitutes; no audio or external requests.

Compare previous enrichment with the optimization using SQL query counters for
mount and each of the four successful read operations. Check default catalog
and lookup status, latest committed draft visibility, deleted/superseded or
wrong-owner status exclusion, revoked identity/provider/binding and changed
record ownership before final publication. Retain existing auth, deadline,
transaction-lock and encounter regression coverage.

The historical Mac43 observation is one sample: 18,826 ms across three requests,
including 15,394 ms receiver wait and 3,415 ms fresh token wait. Individual SQL
duration is unmeasured. Removing 24 SQL round trips is not proof of the four-
second product target; local elapsed times are not physical or staging latency.
JWT/public-key caching, HTTP client lifetime and native phase composition are
outside this change.

## Release and recovery

One backward-compatible Core release after reviewed local evidence. The existing
receiver is already the receiving side of the unchanged contract; no receiver,
API worker, native client, migration or coordinated configuration release is
required. This work does not itself authorize cloud publication or deployment.
After an authorized Core rollout, qualify unchanged legacy/default responses
and final mount status before measuring current synthetic opening latency.

Revert these service changes and deploy the prior reviewed Core artifact if
needed. No data or schema rollback and no client downgrade is required. The
previous implementation simply resumes the extra status reads. Failures retain
the existing denied/unconfirmed response; do not fall back to stale authority
or publish an earlier lookup's status.
