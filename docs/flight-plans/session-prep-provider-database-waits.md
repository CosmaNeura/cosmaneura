# Session Prep provider database waits

Tier 2: provider authority and canonical persistence. This Core-only receiver
increment follows merged PR795's current authority recheck and PR796's shared
provider-chat admission. PR797's content-free maintenance alerts are preserved.

For the existing dedicated Sovereign service workload, install transaction-local
PostgreSQL `lock_timeout` of one second and `statement_timeout` of three seconds
before token, actor-hint, Doctor or lineage reads. Keep any tighter preexisting
limit. Bootstrap receives only the already authenticated service client ID;
its optional internal argument preserves existing callers. Resolve, canonical
conversation writes, conversation reads and the shared interaction/reporting
authorization gates install the same limits. Reinstall after resolve's existing
integrity-conflict rollback because transaction-local settings have reset.

Full authority, current post-lock expiry/revocation, workload/actor binding,
exact replay and every source/history/final fence remain required. A fixed
PostgreSQL lock/query timeout rolls back the wrapped transaction and raises a
value-free unavailable error. Canonical and resolve routes return a safe 503
without an accepted phase, receipt or automatic model retry. Existing exact
usage/audit replay semantics and late observational-reporting rules remain
unchanged. Commit or rollback restores the pooled connection's original limits.
No global engine, Scribe, companion, browser or public-workload limit is changed.
Configured workload IDs do not prove that external credentials are unshared.

Rehearse in this task's explicitly owned synthetic PostgreSQL lab, using
independent connections and actual `pg_blocking_pids`: hold Doctor and lineage
locks through user/final writes; prove bounded denial, no new phase, rollback
releasing the admission lock while the original blocker remains, and successful
same-key retry after release. Check bootstrap/resolve contention, shorter waits
that succeed, actual slow-statement cancellation, preservation of tighter
limits, transaction reset, trusted bootstrap caller propagation, disabled
authority and other workloads. Re-run focused admission, post-lock expiry,
conversation and Scribe capture guards. No reset, truncate, migration, external
SDK, live identity, model call or clinical data is part of this rehearsal.

This bounds each database statement/lock only after connection acquisition.
It does not establish a total transaction deadline, pool checkout, DNS,
connection/TLS setup, driver socket/rollback response deadline, or prove that a
cancelled vendor stopped/refunded work. Core source leaves engine connection
arguments implicit; the local installed QueuePool default is 30 seconds, not
proof of the deployed pool. A content-free staging projection found no explicit
URL connection/socket timeout options; OS/platform defaults are unobserved.
Those remain separate operational limits, alongside actual alert ingestion,
recipient delivery and Clerk logout-to-mounted-context revocation acceptance.

Release Core receiver first, with independently reviewed exact-head evidence,
then the separately reviewed Sovereign sender's 165-second work /180-second
total deadline and truthful closure. The parent task owns staging publication,
image verification, preserved Scribe/public configuration and live workflow
acceptance. Recovery uses the prior immutable Core image, retaining canonical
rows; a revert removes these receiver wait controls. No schema, dependency,
configuration, public schema, UI, workflow, production or cohort change occurs.
