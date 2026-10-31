# Session Prep accepted-answer display

## Problem and result

The fresh conversation read intentionally hides an accepted answer when its
source changes. A provider still needs to open the original reviewed snapshot
and verify a previously displayed or exported answer after correcting a note.
The separate display contract retains that original attribution without making
the old answer available as current inference memory.

## Contracts and ownership

- `read` schema 1.0 and the history tool retain their current freshness rules.
- `read-with-citations` schema 1.1 hydrates only the current accepted answer map.
- `read-retained` schema 1.2 shows an accepted snapshot as `historical` or
  `superseded`. Assistant maps retain the original ordinals, source dates and
  review timestamps. User map and display-state fields are null.
- The retained citation GET opens the exact reviewed version identified by
  the accepted map. It cannot fall back to a current note, match quotations by
  text/date, or accept a model-written URL or identity.
- Core admission owns the immutable full context and history witnesses.
  This consumer reconstructs them only to verify receipts and display sources.
  Provider panel/client and vendored frontend changes are separately owned.

## Technical invariants

Every display request checks current real provider and patient ownership,
record lineage, canonical accepted user/final hashes, receipt retention and
the complete source witness. A history witness selects only its originally
recorded bounded members; it never reconstructs a new subset or recursively
reads a history tool. Corrupt, missing, revoked or mixed-owner evidence cannot
produce an assistant answer or citation map. Legacy answers without a retained
witness continue through the unchanged current-source gate.

Reads remain bounded to six turns, twelve messages and 192 KiB, with current
transaction-local database deadlines and no-store responses. This work changes
no database columns, live settings, source review decisions, provider grants,
worker/scheduler behavior or model/clinical egress boundary.

## Verification and release gates

The source regression definitions cover original content/date/map preservation,
correction with retained display but stale inference, receipt corruption and
expiry, current patient ownership loss, and exact history receipt integrity.
Real database execution, the final normal full OpenAPI export, independent
final-source review and current-head CI are required before publication or a
coordinated separately authorized staging release. Source-only checks are not
evidence that live persistence, export or vendor review has succeeded.

## Recovery

Disable the new display consumer through the previously reviewed release
recovery procedure if its live gates fail. Do not repair accepted maps, relabel
old test results, change current-source inference admission, or substitute the
latest chart content. Preserve immutable accepted phases and reviewed snapshots.
