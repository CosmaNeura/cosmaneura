# Combine adjacent provider inputs during Scribe startup

Tier 2: provider authorization query shape. Owner scope: continue desktop and
mobile recording startup toward five seconds, with local checks and merge
authorized. This changes no schema, credentials, grants, or audio source behavior.

The current merged Core takes separate revocation and member/practice reads
before its default-clock provider access check. The candidate combines those
adjacent inputs only when the existing private proof verifies the exact session,
transaction, subject, provider, practice and loaded owner rows. It samples the
clock after this read and still reads current grant evidence afterward. Invalid,
expired, detached or mismatched ownership, explicit clocks and other callers
retain their previous query paths. Every original access decision and source
permission boundary remains.

Validation uses the owned fictional PostgreSQL lab and generated identities.
Compare the default-clock decisions against the previous implementation for
pilot, membership, entitlement, BAA and cutoff cases; preserve the clock position
and late grant read; run provider/capture/window scope and concurrency tests.
Profile the actual current Core services with the same virtual Scribe responses,
retaining numeric counts and code call sites only. The initial candidate removes
25 desktop SQL statements (751 to 726) and 39 across the complete mobile profile
(1,107 to 1,068). Instrumented localhost durations are not device startup times.

Before merge record the exact final tested commit and implementer review in the
PR. Staging promotion remains a separate artifact qualification and current-state
reconciliation operation under the existing owner release authority. Recovery is
the prior verified Core image/configuration or reverting these two service
changes; no migration or destructive data recovery is involved. No production,
physical microphone, live clinical record, or other-task access is authorized by
this plan.
