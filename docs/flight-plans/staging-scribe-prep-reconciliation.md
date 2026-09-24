# Explicit staging Scribe and Session Prep reconciliation

## Outcome and authority

Tier 2: this is an operator-only additive data repair, including one explicitly
owner-directed staging pilot practice membership. It does not change token
exchange, any public route, release configuration, schema or application image.
The owner authorized staging integration and subsequently explicitly requested
membership for the two existing testing accounts. One already has membership;
the other must be added. Exact identities stay in the private operator plan,
not in this document, source code, tests or independent-review prompts.

Existing reviewed Scribe captures may predate `ProviderPracticeAuthority` and
`ProviderRecordAuthority`. When their provider joins the Session Prep cohort,
the runtime correctly refuses automatic adoption of the old tenant and record
binding. The operator validates that the reviewed capture scope and current
clinical ownership agree before appending the missing mappings. It never
changes or regenerates actor, tenant, identity, person, encounter, capture,
consent, session or review identifiers.

## Bounded operation

`scripts/staging_reconcile_scribe_prep.py` runs only inside the fixed staging
Core app against the fixed staging PostgreSQL host and database. Both doctors
must already be active onboarded pilot accounts, explicitly selected by the
existing Session Prep cohort configuration. No cohort or pilot grant is added.
The additional account must have no membership, or an already active membership
in the same practice. Revoked, inactive, managed-roster and other-practice
memberships are not repaired or moved.

The requested membership is an explicit operator action. It is not a claim of
a self-service workforce acknowledgement. Its attestation fields remain null;
access continues to depend on the already-existing pilot grant. A standalone
practice can retain its legacy solo enrollment setting only with the separate
`--preserve-solo-enrollment` selection, bound into the reviewed plan. That
selection does not open public join-code enrollment or alter owner closure
rules. It is for the specifically authorized staging test membership, not a
new general enrollment path. Organization-owned and negotiated-BAA reserved
practices, managed-domain identities and roster assignments are refused. Practices with a
Stripe subscription are refused, and the normal active-seat ceiling is checked.
No BAA, billing, trial or public enrollment settings are changed.

The existing provider must have one unambiguous active provider tenant, one
active reviewed Scribe provider link for the practice, and one unshared person
binding, with exactly one binding and capture in that tenant. A second legacy
capture requires a different reviewed reconciliation, before any mapping is
added. All capture scope columns and the stored digest must match. Existing
identity, actor, tenant, link, membership, person and binding tombstones deny.
The original Scribe staging provenance marker may be supplemented with the
current patient reference, but is never replaced. Another patient reference or
unknown import provenance is refused. Existing conflicting practice or record
mappings are refused. The final state must pass the ordinary runtime
`validate_provider_record_scope` in the same transaction.

## Plan, review and apply

1. Select a clean, merged and independently Cursor-reviewed operator commit.
   Check exact source file hashes and the unchanged deployed Core source and
   schema. Do not execute a draft or use another task's working checkout.
   Normal execution is `python -m scripts.staging_reconcile_scribe_prep` from
   the repository root (`/app` in the image). Do not execute the script by
   path with only `scripts/` on Python's import path.
   When the deployed image predates this operator, retain its exact digest and
   verify the imported `app` helper files and activemodel version against the
   locally tested baseline. A private execution wrapper may load only the two
   exact merged operator files into memory with verified SHA256 hashes, then
   invoke the same CLI `main()` and arguments. It must not replace application
   files, alter helpers or invoke `reconcile` around the CLI's target checks.
   Retain the wrapper and dependency-hash receipt with the concrete plan.
2. Invoke the script without `--confirm-plan-sha256`, with the exact additional
   Doctor, existing capture binding and stable opaque operator reference. The
   owner-directed membership flag records the already-received authorization;
   it is not itself a substitute for owner authorization. The dry run returns
   scope identifiers and fingerprints only, then rolls back.
3. Inspect the concrete plan. Preserve its hash, source commit/file hashes,
   current staging revision and owner authorization in private local evidence.
4. Apply with the exact plan SHA. The script opens a READ COMMITTED transaction,
   locks pilot lineage before practice/doctor rows, rechecks current access and
   scope, and appends the membership, mapping, provenance and operational audit
   rows atomically. No network model, messaging or billing call occurs.
   Fresh statement snapshots after the advisory/row locks observe a concurrent
   committed roster import or seat activation. A repeatable/serializable
   snapshot could predate a lock wait and is deliberately refused. Tenant and
   person row locks also fence new foreign-key dependent binding inserts.
5. Re-observe the rows and capture digest. Verify fresh authenticated dashboard
   Session Prep and native Scribe reopening. Continue synthetic note review,
   later-visit Prep, correction invalidation and persistence verification.

An exact retry before intervening scope changes is a no-op, authenticated by
the retained operation audit and plan hash. Changed or ambiguous scope is a
refusal, never a reason to issue replacement capture keys. A transport timeout
requires read-only observation before any retry.

## Validation and recovery

Run the PostgreSQL reconciliation tests in the owned disposable capture
database. They exercise the actual authority exchange and Scribe reopen after
the repair, unchanged capture identity/digest, retained provenance, exact retry,
plan drift and current-access refusals, and rollback after a failed final
postcondition. Run surrounding authority/capture tests and Python checks, then
independent Cursor review after the final substantive push. GitHub Actions is
unavailable; local results are the primary verification.

The review follow-up adds real two-session advisory-lock snapshot checks,
reserved-practice/managed-domain/extra-binding refusals, the seat ceiling, and
the added provider's own-record exchange plus cross-provider denial. Those
checks do not claim a concurrent full roster import, outer CLI commit/rollback
or cross-transaction retry: the operator fixtures retain outer rollback and
use savepoints. Exact retry remains limited to before intervening scope changes.
Adding a workforce member leaves the existing owner-closure rule in force;
that rule requires revoking the workforce member before closing the owner.
CLI failures expose only a fixed execution phase, never exception values.

Before commit, any error rolls back the whole operation. After commit, retain
the additive mappings and audit history. They have no rollback-by-deletion
path; resolve a confirmed issue with a reviewed forward repair. Removing the
provider from the Prep cohort is not a compatibility rollback: a record that
now has real-record scope is revalidated on every exchange and may then deny
Scribe too. The existing image recovery procedure remains separate and must
not remove these records. This change does not deploy a new application image
or authorize production, additional memberships, record reassignment, other
clinical data, or a public synthetic-fixture fallback.
