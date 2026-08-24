# Pilot continuity reconciliation

The original legacy-pilot grant remains a closed cohort: 47 accounts in
production and 6 accounts in staging. Do not regenerate that cohort to add a
missed operational user. Use a separate continuity amendment so the base
digest remains independently auditable.

An amendment grants only the existing pilot bypass. It does **not** create or
modify a Doctor, practice membership, organization roster, BAA acceptance,
billing entitlement, administrator role, or onboarding timestamp.

## Private manifest

Create the manifest outside the repository, set its mode to exactly `0600`,
and use a different file for staging and production. The command refuses
symlinks, other permission modes, files over 1 MiB, unknown fields, duplicate
subjects/emails/Doctor IDs, and a manifest whose environment or Clerk issuer
does not match the running deployment.

```json
{
  "schema_version": 1,
  "environment": "staging",
  "identities": [
    {
      "issuer": "https://<exact-environment-clerk-issuer>",
      "subject": "<exact-clerk-subject>",
      "verified_primary_email": "<normalized-verified-primary-email>",
      "doctor_id": "<exact-doctor-typeid>",
      "continuity_basis": "reviewed_operational_user_omission",
      "practice_id": "<practice-typeid-or-null>"
    }
  ]
}
```

Every identity must contain all six fields and must name an existing Doctor by
its exact TypeID. The command never pre-authorizes a person who has not signed
in and never treats a future Doctor as a wildcard binding. `practice_id` is
optional, but it must exactly match the Doctor's current active membership (or
be null when there is no membership).

## Dry-run and apply

Run in the target backend environment so the command uses that environment's
database, Clerk secret, exact configured issuer, and deployment name.

```bash
python -m app.cli amend-pilot-continuity \
  --manifest "$PRIVATE_CONTINUITY_MANIFEST" \
  --source-ref "<durable-reviewed-source-reference>" \
  --operator-ref "<change-or-ticket-reference>" \
  --reason "<approved-operational-omission-reason>"
```

The dry-run performs live Clerk verification and database reconciliation and
prints only aggregate counts plus the manifest and plan SHA-256 digests. Review
and record both digests. Apply the byte-identical file and exact arguments:

```bash
python -m app.cli amend-pilot-continuity \
  --manifest "$PRIVATE_CONTINUITY_MANIFEST" \
  --source-ref "<durable-reviewed-source-reference>" \
  --operator-ref "<change-or-ticket-reference>" \
  --reason "<approved-operational-omission-reason>" \
  --confirm-plan-sha256 "<dry-run-plan-sha256>" \
  --apply
```

The apply re-verifies Clerk and all database facts, then writes the amendment,
identity evidence, and grants in one transaction. A digest mismatch, duplicate,
deleted/revoked account, orphaned Doctor/practice, mismatched membership,
conflicting roster, ambiguous authority identity, or existing grant history
refuses the entire batch. Output never includes emails or Clerk subjects.

If a Clerk subject changed for an existing Doctor, stop. This command will not
silently move authority. Use the existing `rebind-doctor-clerk-identity` dry-run
and hash-confirmed flow, which preserves the Doctor and clinical records and
emits its own audit evidence. Its plan and receipt are bound to the running
environment and configured Clerk issuer. A continuity-granted Doctor without a
practice membership remains eligible for this audited recovery; the operation
does not create practice, BAA, billing, or onboarding state.

## Audit

```bash
python -m app.cli audit-pilot-access
```

The aggregate report validates the immutable base cohort separately from
amendments and understands both ordinary revocations and audited identity
rebind chains. A valid base plus valid amendments/revocations/rebinds remains
`cohort_integrity_valid: true`; amendment and rebind counts/digests are reported
without identities.

An omission for a person who has never signed in is not eligible for this
continuity mechanism. Let the normal account creation path establish the Doctor
first, then review an exact identity-bound amendment if operational continuity
is still required.
