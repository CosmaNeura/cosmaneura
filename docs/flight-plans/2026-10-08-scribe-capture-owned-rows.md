# Scribe capture rows in their owning transaction

Tier 2 under the owner's all-twelve repair goal and approved backend latency
release. Capture's provider checks repeatedly open separate ActiveModel
sessions even though the caller already owns the exact Practice and Doctor
locks. Bind only the synchronous capture-policy scope to its existing Session
and expose the existing private row-ownership proof. Preserve Practice -> Doctor
lock order, post-wait membership/identity rechecks, every complete authorization
decision and every pre/post-I/O consent check. Use fresh joined reads only while
the proof matches the exact session, transaction, subject and loaded rows.
Commit, rollback, savepoint, detachment or scope exit invalidates the proof.
The context is restored before any remote operation. No permission is cached.

Validate real PostgreSQL native receipts and exact lock sequence against the
previous query path, complete access decision parity, transaction invalidation,
exception cleanup, concurrent offboarding and all five remote revocation
boundaries. Record the exact reviewed/tested commit, commands and results in
the PR; implementer review and passing merge use September 25 owner authority.
Query-count reduction is not an installed startup-time result.

Release this Core-only change through the existing immutable staging build,
publisher and image-only release adapter. Preserve all role configuration,
secret references, identities, schema and recovery revisions. No Scribe client
or receiver rollout is required. Revert to the prior immutable Core digest for
recovery. No production, cohort, grants, billing, schema or migration changes.
