# Seat locking, billing summary, and owner roster

Tier 2: the stack changes billing-related membership concurrency and a shared
access decision. This plan covers PR 523 (practice seat locking), PR 520
(owner billing summary), and PR 522 (owner roster), in that dependency order.
The reconciled baseline is deployed Core
`9f09bafa87bfb468a627f01d27ff31791c07c9f5`, included in all three heads reviewed
on September 29. Each PR remains a separate deployable outcome within the
2,000 changed-line / 50-file budget. Invitation and member-revocation feature
PRs are outside this plan.

PR 523 requires the practice row lock for membership inserts, deletes,
seat-state or revocation changes, practice moves, and recorded Stripe quantity
obligations. Both practices must be locked before a move. All existing
application mutation paths use the helper, including staging Scribe/Session
Prep reconciliation. ORM bulk statements and raw SQL do not enter the flush
guard and must not be introduced for membership writes. Companion provisioning
fixtures also take the real lock; the runtime guard is never bypassed.

PR 520 adds an owner-only billing summary from persisted Core state. Pending
quantity synchronization hides an unconfirmed billed count. Reads make no
Stripe request. PR 522 adds the owner's practice-scoped names-only roster,
sharing the seat predicate and existing effective-access decision. Ordinary
members cannot request it. Current BAA, membership, entitlement, and
capability-bound read-only support view-as rules remain enforced. The roster
exposes attestation names and access state, without emails or identity IDs.

There is no schema migration, backfill, new dependency, provider contract,
Stripe price change, deployment configuration change, or cohort expansion.
No migration rung is required. Existing Core/Scribe/Session Prep configuration
and deployed artifacts must be preserved unless a separately authorized
release explicitly replaces them. Source changes do not grant permission to
run billing operations or admit live patient data.

Before merging, record the exact final tested/reviewed SHA and local results
on each PR. Validate seat-lock transaction/savepoint/move cases; real
PostgreSQL last-seat and billing-state races; create/join/roster claim/revoke,
bootstrap and account-closure paths; owner/workforce/lookup summary and roster
authorization; access-decision equivalence; and frontend billing/roster/settings
tests plus TypeScript checks. Run current W8 capture, Session Prep, provider
authority, and companion provisioning regressions with their explicit owned
disposable database URLs so skips do not hide newly added membership fixtures.

The global pytest hook cleans up Clerk users, migrates, truncates, and seeds.
Inspect the selected harness first and use synthetic credentials with external
effects disabled. The companion suite uses
`--confcutdir=tests/authority_kernel` and its exact loopback
`cosmabot_companion_test` database and role. Its SQLModel table setup proves
model/constraint behavior, not migration-trigger coverage. Test results belong
in PR evidence; this plan does not assert an unrun check passed.

Release only merged, passing immutable commits through the owner-authorized
staging process after recording current environment SHA/digest/config/schema
drift and the explicit keep/assimilate/replace decision. Check normal Scribe
and Session Prep access alongside the owner billing/settings reads, workforce
denial, and seat-summary consistency. Monitor `PracticeSeatLockNotHeld`,
`billing_seat_summary_ledger_invalid`, and roster 5xx errors. Any unexpected
lock rejection or access regression stops promotion and requires repair or
recovery at an exact reviewed commit. Production remains separately gated.

Recovery is a reviewed revert of the affected PR, preserving current data.
If unwinding the full stack, revert PR 522, then PR 520, then PR 523 so readers
are removed before their shared helpers. Deploy that merged recovery candidate
through the same authorized path; do not blindly deploy the historical
baseline over newer unrelated work. There is no destructive data rollback.
Reverting seat-lock enforcement restores the earlier concurrency behavior,
so do not introduce new membership mutation paths until locking is restored.
