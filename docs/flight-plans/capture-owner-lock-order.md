# Capture owner lock ordering

Risk: **Tier 2**, because this changes the order of locks at an existing
authorization boundary. Base: `403d8919ce3b4de44ff87fe377599d88d8e1e20c`.

## Defect and scope

Capture's first `authorize_interaction` takes Doctor through the mandatory
view-as guard, then the scope resolver later takes Practice. Real-record token
exchange and roster/offboarding take Practice before Doctor. Concurrent
transactions can therefore request these two shared resources in opposite
orders. The offline regression executes the actual scope, authority, view-as,
and exchange owner-lock helper against controlled synthetic rows and observes
that inversion. It does not prove a PostgreSQL deadlock or the cause of the
reported device upload failure.

Only capture opts into a pre-view-as callback, after the existing opaque-token
and session validity checks. The callback re-resolves provider context, derives
the Doctor from the stored, configured-issuer identity, then locks Practice and
Doctor. Appointment/request/link identifiers cannot select another owner.
Membership, mount and identity are checked again after waiting. The original
view-as, locked-token, lineage, authority-row, reviewed-link, clinical-owner and
processing-access checks still run. The provider-only context checks duplicate
`authorize_interaction`; the separate Session Prep allowlist is not added.

After the shared Practice/Doctor pair, capture retains its existing order:
ViewAsSession (when present), token, interaction lineage, mount, actor, source
identity, tenant, person binding, person, membership, reviewed capture link,
practice member, appointment and patient. Binding and admission locks follow
scope authorization. Reacquiring the already-held Practice/Doctor is harmless.
This change does not establish a global lock order for unrelated services.

## Validation and release

- Preserve a failing baseline lock-order regression and passing final scoped
  checks, exact source hashes, commands and independent review evidence.
- Exercise invalid tokens, concurrent identity/mount/membership changes, source
  revalidation and the actual view-as guard using synthetic controlled rows.
- Existing PostgreSQL scope/admission suites require an explicitly scoped
  disposable database; skipped tests are not acceptance evidence.
- No schema, configuration, API contract, auth policy, service or dependency
  change; no migration or feature flag is needed for lock ordering.
- This work is local only. Any release requires independent final source
  review, the normal merged-source provenance gate and a separately authorized
  founder-controlled staging deployment. No deployment authority is implied.

## Recovery

Before deployment, retain the current release. After an authorized deployment,
rollback uses the prior immutable service artifact and does not mutate saved
recordings, receipts, tokens or clinical records. That restores the prior lock
inversion susceptibility, so a rollback is not evidence of incident resolution.
