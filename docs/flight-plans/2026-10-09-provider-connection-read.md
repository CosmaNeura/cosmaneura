# One fresh provider connection read inside an owned Scribe lookup

Tier 2. Continue all twelve Scribe UX repairs and the consistent 5–7 second
consent-to-Ready goal. No identity, consent, access rule, cohort, billing, schema,
configuration, credential or deployment-control change.

The initial provider read in a synchronous encounter lookup takes the original
Practice→Doctor locks and view-as guard, then separately reads identity, actor,
provider link, tenant, membership and practice. Combine those current connection
inputs in one query only while the existing exact lock evidence matches the
lookup's current ActiveModel session, transaction, subject and loaded provider.
Keep the original post-wait membership recheck, Doctor/member/practice access
decision, authority/link/member locks, record validation and final fresh lookup.
No result becomes permission for a later decision or remote call.

The existing unique identity, active actor/tenant provider link, authority
membership and one-practice membership constraints preserve query cardinality.
Keep identity-map behavior and the individual original lock sequence. A current
or missing component denies. Invalid/mismatched/expired proof and transaction or
savepoint changes select the original individual-query path. Catalog/setup and
other non-lookup calls retain their original behavior; never set/reset the
ActiveModel session. No transaction or proof spans an await.

Qualification uses the actual native services on task-owned fictional PostgreSQL:
full receipt parity and exact lock statements, all access decisions, current
actor/identity/link/tenant/membership/Doctor/label changes, committed revocation,
post-view-as same-transaction changes, transaction/savepoint fallback, default
catalog behavior, stale ORM, real writer serialization and exception cleanup.
Record the exact committed broader tests, lint/format/types, five-stage DBAPI
SQL/deadline comparison, local budget and implementer review in the required PR.

Use the existing immutable source-bound staging release with unchanged schema,
five-workload configuration/secrets and Core 718/receiver 38 recovery. No migration.
Keep the 20 GiB build guard. Desktop 64 installation/measurement needs manual Mac
unlock. Virtual audio only; never press installed Start for physical capture.
Recovery: revert this change or retain Core 718 staging digest
sha256:92bdb2e4c5f1261c0a0a4738aef42da0d57f630f0daff75b1c2ae7909246fa73.
Production is outside scope; all-twelve and 5–7 second acceptance remain open.
