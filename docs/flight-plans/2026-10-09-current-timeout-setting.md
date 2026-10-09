# Avoid reapplying an equal transaction-local deadline setting

Tier 2. Recording preparation pays a separate round trip to apply its current
statement/lock deadline before every actual SQL statement. When the exact single
record budget scope, capture budget, owner thread, psycopg connection and current
SQLAlchemy transaction/savepoint still match, an equal or tighter setting can
remain for a generated relational SELECT with no text, literal SQL, function
expressions or embedded writes.
Calculate both current limits and retain both cancellation fences on every call.
Never round, lengthen, omit or infer a deadline; only reuse proven current values.

Text, literal SQL, functions, writes (including CTEs), absent/mismatched scope,
adapters, failed/ended database
transactions, changed budget, transaction or savepoint retain the original setter
and invalidate correspondence. Nested record guards retain their original setter
order. Setting success is recorded only after both fresh cancellation checks.
Scope exit discards the setting. No access, identity, credentials, row ownership
or future permission is retained; all authorization SQL and individual locks stay.

Qualify in task-owned fictional PostgreSQL: actual setter reduction with identical
complete native receipts/SQL/locks, fresh shorter budgets, cancellation on reused
settings, slow SQL/lock cancellation, commit/rollback/savepoint changes, failed
SQL, text/function/mutation invalidation, nested scopes, different connections,
threads and adapters. Record exact committed suite, lint/types, budget and
implementer review. Counts do not establish real-device startup seconds.

No schema, consent/access/billing rule, configuration, credentials or cohort
change. Same immutable merged-source staging qualification and unchanged 20 GiB
capacity guard. Reconcile five workload configurations/templates/secret hashes
and schema/restore rehearsal. Preserve Core 718/receiver 38 recovery. Desktop 64
installation/timing requires normal close/manual unlock. Virtual audio only;
production excluded; all twelve repairs and 5–7 second acceptance remain open.
Recovery: revert or retain Core 718 staging digest
sha256:92bdb2e4c5f1261c0a0a4738aef42da0d57f630f0daff75b1c2ae7909246fa73.
