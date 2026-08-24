# Superadmin capability operations

Superadmin authority is separate from `DoctorRole.admin`. Runtime checks use
an active grant matching all five fields: deployment environment, capability
kind, exact Clerk issuer, exact Clerk subject, and Doctor ID. Email is never a
runtime authorization input. A `platform_admin` grant also does not imply a
`view_as` grant; grant both explicitly when both are approved.

No person or Clerk subject is seeded by this migration. The initial approved
operators must be granted separately in staging and production from each
environment's exact live identity. A future candidate who has no account in an
environment cannot be pre-granted authority: create and verify the account
first, reconcile its Doctor row, then prepare a new reviewed manifest.

## Private manifest

Prepare this JSON outside the repository. Use the current deployment's exact
configured Clerk issuer and values confirmed from that same Clerk instance:

```json
{
  "schema_version": 1,
  "environment": "staging",
  "clerk_issuer": "https://example.clerk.accounts.dev",
  "accounts": [
    {
      "clerk_subject": "user_REDACTED",
      "verified_primary_email": "operator@example.invalid",
      "doctor_id": "doctor_REDACTED",
      "capability_kinds": ["platform_admin", "view_as"]
    }
  ]
}
```

The file must be a regular file owned by the invoking user with exact mode
`0600`. Symlinks, extra keys, duplicate identities, duplicate capabilities,
unknown environments, malformed TypeIDs, and more than 32 accounts fail
closed. The repository ignores files ending in
`.superadmin-capabilities.private.json`, but the preferred location is an
owner-only temporary directory. Delete the local copy through the approved
secure-data process after preserving the controlled source elsewhere.

```shell
chmod 600 /secure/path/staging.superadmin-capabilities.private.json
```

Omit `--manifest` to enter one target through hidden prompts. Do not place
identity values directly on the command line or in shell history.

## Grant

Always run the dry-run first in the target deployment. It checks that the
manifest environment and issuer match the process, gets each exact subject
from live Clerk, requires one verified primary email and an unlocked/unbanned
account, and reconciles the exact active Doctor row. Output contains only
counts and SHA-256 digests.

```shell
python -m app.cli grant-superadmin-capabilities \
  --manifest /secure/path/staging.superadmin-capabilities.private.json \
  --source-ref change:reviewed-superadmin-cohort \
  --operator-ref ticket:approved-change \
  --reason-code initial_reviewed_grant
```

Copy the dry-run's `plan_sha256` exactly, then rerun the same command with:

```shell
  --confirm-plan-sha256 PLAN_SHA256 --apply
```

Apply revalidates live Clerk and locked database state and commits all grants
and append-only audit rows in one transaction. Exact retries are no-ops. A
partial, conflicting, ambiguous, deleted, or identity-drifted cohort refuses
the entire operation.

## Revoke

Use the same exact manifest fields and a new opaque change reference and
reason code:

```shell
python -m app.cli revoke-superadmin-capabilities \
  --manifest /secure/path/staging.superadmin-capabilities.private.json \
  --source-ref change:reviewed-superadmin-revocation \
  --operator-ref ticket:approved-revocation \
  --reason-code access_withdrawn
```

Review the dry-run and apply with its exact plan hash. Revocation intentionally
uses retained identity evidence rather than requiring Clerk to be available;
this keeps emergency offboarding possible for a locked, banned, or deleted
Clerk account. It revokes authority without deleting the Doctor or any clinical
record. Grant and audit evidence is retained for at least seven years.

## Aggregate audit

```shell
python -m app.cli list-superadmin-capabilities
```

The command reports only environment-scoped totals, counts per capability,
audit count, and a digest of the active bindings. It never prints Clerk
subjects, Doctor IDs, email addresses, tokens, or PHI.

Production availability of these commands is not production authorization.
Run production grant or revoke operations only through the separately approved
release/change-control gate.
