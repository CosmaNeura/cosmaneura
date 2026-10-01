# Existing-practice ownership correction

Tier 2: an operator changes administrative authority. This command implements
the owner's explicit request to correct one staging practice's designated
owner. It does not introduce a public route or change any product-access gate.

## Scope and prerequisite evidence

Use the exact practice ID, its one current owner's Clerk subject and the new
owner's existing workforce subject. Bind each subject to its fresh verified
primary email through the existing Clerk adapter; compare both with the locked
Doctor rows and explicitly confirm the new owner's email without terminal echo.
Identity overlap or disagreement must be resolved before transfer.

The command defaults to a dry run. It requires two existing, active memberships
in the same ordinary practice and exactly one owner. Disabled Doctors, revoked
pilot grants, revoked/ending memberships and organization-managed practices
are rejected. Missing BAA or billing entitlement does not grant or deny this
privileged correction; ordinary clinical processing remains independently
gated by those unchanged rules.

## Atomic apply and evidence

The reviewed plan hash binds the practice, both Doctor/member identities and
their current state, verified-email binding, operation reference, operator and
reason code. Apply refreshes these facts under the practice-seat lock, followed
by both Doctors and memberships in stable order. A changed plan is refused.

One transaction changes the old owner's role to workforce, promotes the new
owner and appends a `PracticeAccessAudit` record. Its existing string action
column permits `practice_ownership_transferred` without a migration. Evidence
contains stable identifiers and references plus the plan hash, never email,
name, clinical content or legal text. No account/membership is deleted, moved,
revoked or created. Seats, attestations, BAA history, billing, grants and provider
or clinical ownership are retained.

Replay uses the exact practice and operation reference under the same lock.
Matching audit/request bindings, hash and resulting roles return the existing
receipt without another mutation. Conflicting or duplicate evidence is denied;
no automatic retry is performed after an uncertain database outcome.

## Validation and release

Run isolated PostgreSQL regression tests for the actual service, including
preview, stale state, owner cardinality, identity disagreement, hard denials,
role/audit atomicity and replay. Test the CLI's hidden confirmation, default
dry run and required apply hash. Use an owned synthetic database without the
repository-wide Clerk cleanup or database-reset hooks.

Merge the exact reviewed/tested source under the owner's local-validation
policy. For the authorized staging correction, separately pin that source,
the deployed Core image/revision/schema, the unchanged imported dependencies,
the exact old/new subjects/practice and the reviewed preview hash. Any one-off
operator invocation must execute the merged, verified service and preserve
the server files and configuration. A web deployment is not implied.

## Recovery

Before commit, transaction rollback retains both original roles and leaves no
audit. If the result is uncertain, inspect the exact immutable operation
receipt and current owner; do not blindly issue a new operation reference.

Reverting this code does not reverse a committed transfer. A later reversal
requires the new owner's explicit decision and a fresh, hash-bound transition
from the then-current owner back to the retained workforce member. Preserve
the original audit and append a new one; never rewrite legal history or restore
the database as ownership recovery.
