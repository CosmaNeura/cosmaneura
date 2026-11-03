# Production startup migration cutover

Tier 2; source proposal only. No production execution is authorized by this file.

Production's current Core image defaults to startup migration when the variable
is absent. Read-only inspection on November 1 found it absent on the API,
worker and scheduler. The source-controlled shared runtime map now supplies
`AUTO_MIGRATE_ON_STARTUP=false` to all three, independently of Session Prep's
activation switch. Generic configuration cannot shadow that setting.

Before the Session Prep schema upgrade, review and apply a fresh full saved
infrastructure plan that changes only this runtime setting while retaining the
actual current images, configuration, identities, secrets and Scribe settings.
Verify all three ready revisions carry the setting. Reject unrelated plan
changes; do not use a targeted apply or an unqualified historical plan.

The existing production migration job remains the sole upgrade executor.
Bind its actual process, admitted candidate/digest, database reference and
current head before running it. The earlier November 1 head observation is
historical; it does not authorize a migration from that state. Reconcile a fresh
database head against the final admitted candidate's complete migration graph,
including the medication enum expand and practice-binding migration. Verify
strict database TLS and a recoverable
backup, agree a bounded lock window, then migrate before deploying the new
API, worker and scheduler images.

Keep the setting false after partial failures. Preserve additive schema and
record actual job/schema/image/revision state; use a reviewed forward fix.
Removing this source field would restore the application default and is not
an approved schema or runtime recovery. No database downgrade, Scribe change,
provider activation or production resource mutation is part of this proposal.
