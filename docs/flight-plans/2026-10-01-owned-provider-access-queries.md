# Native provider access query composition

Risk: **Tier 2**, access-adjacent Core performance change. No schema, data,
configuration, identity policy, token, consent or capture changes.

## Outcome and boundaries

The native facade's repeated provider checks spend six SELECTs resolving
ordinary no-pilot-grant access. Combine three pairs only at the final
`companion_encounters._provider` check, where the existing Practice then Doctor
row locks and post-wait ownership recheck have completed. All access decisions
still execute. The original early tombstone and late active-grant stages stay
separate; the decision rules, cutoff operators, identity-map behavior and
ambiguity refusal remain shared with the original path.

The explicit proof is minted by the actual lock-taking factory. It is bound to
the exact Session, innermost transaction, Practice, Doctor and subject, and
checked against the link-selected Doctor and practice. Scope exit or any
transaction/savepoint transition invalidates it. Missing/mismatched evidence
selects the original queries. No ambient context, cached authorization result,
additional lock, lock reordering or proof crossing an await is introduced.

An earlier global query composition was rejected after three real unlocked
concurrency tests produced old-deny/new-allow outcomes. Those original read
boundaries remain for every other caller. Grant mutation services take the
existing Doctor lock; practice/member access changes take the Practice lock.
This relies on existing application writer discipline, not a new database
predicate lock against arbitrary administrative SQL.

## Local validation

Use the isolated authority test bootstrap and explicitly owned local synthetic
PostgreSQL database. Never collect the repository-wide integration conftest.
Run original-query parity and unlocked race regressions, scoped proof tests,
actual native route query/lock counts, and the applicable native/authority suite.
Verify actual billing, workforce and pilot revocation services block before
mutation, then commit and cause the next native opening to deny. Inspect side
effects as well as responses. Verify ownership changes winning the lock wait,
invalid proof fallback, transaction/savepoint expiry, identity-map/cardinality
and cutoff/default-clock parity. Run focused source lint/types and diff review.

The synthetic no-grant opening model predicts 52 of 125 access calls using
three queries: 2895 to 2739 SELECTs overall (156 fewer, 5.39%). Actual pilot and
organization paths differ. This is not a four-second or physical recording
qualification; real identity/network/device/paint/audio time is excluded.

## Release and recovery

One backward-compatible Core-only change; no receiver or native release is
required and existing receiver-first compatibility is unchanged. No migration,
backfill, reset or deployment is part of this patch. Record the exact locally
tested commit and implementer review before the authorized PR/merge process.
Deployment remains a separate explicitly selected staging release.

Rollback is a code revert to the original shared query path and provider lock
statements. No persisted proof or new state exists to migrate or clean up.
