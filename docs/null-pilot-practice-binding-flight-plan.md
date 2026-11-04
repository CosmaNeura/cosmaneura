# Bind one NULL legacy pilot practice snapshot

Risk: **Tier 2**, access authority and additive retained evidence. This source
outcome is local preparation only. Publication, migration execution, deployment
and any actual grant change need separately reviewed authorization.

An eligible existing provider can have an original pilot grant whose practice
snapshot is empty. Session Prep correctly refuses that grant as practice
entitlement. This repair leaves that strict predicate unchanged. An explicit
hash-confirmed CLI operation revokes the original grant with seven-year
retention, inserts one same-subject successor for the exact currently authorized
practice, and adds a distinct immutable binding record and practice audit receipt.
It changes no Doctor, membership, BAA, billing, role, flag or visit ownership.

Only a singular started unrevoked original grant with NULL practice, exact
doctor-or-NULL snapshot, unchanged Clerk/email identity and intact cohort
evidence qualifies. The provider must be live and onboarded, with one current
attested seat-holding membership and current BAA. Organization-managed practices,
wrong nonNULL snapshots, revoked/future/multiple grants, and amendment or
identity-rebind lineage require a different review; this command refuses them.

The dry run prints only environment and plan digest. Its request binds exact
Doctor/practice/predecessor IDs, identity/email/before-state hashes, source
commit, UTC observation, operator and reason. It adds the current eligibility
and original-evidence hashes. A new write requires observation age <=15 minutes
and exact confirmed digest. Source provenance and operator authorization must
also be established by the separately approved application packet; a CLI
parameter is not proof that its source commit or operator was authorized.

The applier refuses pending unrelated ORM work before SQL. It locks lineage,
then practice -> Doctor -> member -> grant/evidence, refreshing rows and time
after waits. It verifies the entire closed cohort before mutation and again
after the new binding flush. The predecessor flush releases existing active
subject/Doctor/email unique indexes before successor insertion. Any error rolls
back predecessor, successor, receipt and binding together. An exact retry checks
retained evidence and current eligibility, and never restores a revoked successor.

## Migration and activation rungs

1. Generate genuine Alembic revision `6bd4416fe39c`, parent `312b217ebba4`,
   using the installed library without autogenerate, app bootstrap or DB calls.
   Generation receipt is retained with the local source packet.
2. Rehearse additive DDL on an isolated synthetic PostgreSQL database after
   authorization. Check foreign keys, unique plan/predecessor/successor/receipt,
   append-only UPDATE/DELETE refusal, actual competing transactions, cohort audit
   and rollback. Offline tests compile PostgreSQL DDL; they do not substitute
   for this database rehearsal.
3. Expand the immutable evidence table before deploying its readers. Keep all
   Core readers/worker/scheduler and current Scribe pairing verified. No grant
   changes occur during schema or source deployment.
4. Obtain a fresh exact-account NULL diagnostic, same source/schema/pairing
   binding, and separately reviewed application plan. Dry-run and confirm the
   exact digest for that single eligible account only.
5. Apply once and verify unchanged original snapshots, complete retention,
   one successor, genuine binding receipt, valid unchanged-size base cohort,
   current provider discovery and own-visit access. No broader cohort is implied.

## Recovery and verification

Before application, leave the additive table empty and dormant; do not drop it.
After application, retain compatible binding-aware audit readers. Reverting to
older audit code would incorrectly count the successor as another base grant.
Never rewrite historical snapshots or delete evidence to make an old audit pass.
If access must be withdrawn, separately authorize the existing explicit grant
revocation path, preserving the binding chain and retention; do not restore the
NULL predecessor. A failed transaction leaves the original grant unchanged.

Offline checks execute the actual planner/applier on synthetic transactional
objects and the actual aggregate audit on synthetic cohort evidence. They cover
CAS drift, lock order, concurrency, stale observation after waiting, exact retry,
revocation, practice changes, corrupt evidence, pre/post audit refusal, dirty
session refusal, retained lineage and PostgreSQL DDL/model compilation. Final
independent review is pinned to the local committed source; it is not GitHub
approval or production/deployment authorization.
