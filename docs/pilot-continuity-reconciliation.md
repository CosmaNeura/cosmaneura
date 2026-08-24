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
      "doctor_id": "<doctor-typeid-or-null>",
      "safe_prelogin": false,
      "continuity_basis": "reviewed_operational_user_omission",
      "practice_id": "<practice-typeid-or-null>"
    }
  ]
}
```

Every identity must contain all seven fields. Existing accounts use their
exact Doctor TypeID with `safe_prelogin: false`. A person who has never signed
in may use `doctor_id: null` with `safe_prelogin: true`; the command proves no
Doctor already owns either the subject or email and does not create one.
`practice_id` is optional, but for an existing Doctor it must exactly match the
current active membership (or be null when there is no membership). For a
prelogin identity it may document an existing intended practice without
creating membership.

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
silently move authority. Use the existing `rebind-doctor-identity` dry-run and
hash-confirmed flow, which preserves the Doctor and clinical records and emits
its own audit evidence.

## Audit

```bash
python -m app.cli audit-pilot-access
```

The aggregate report validates the immutable base cohort separately from
amendments and understands both ordinary revocations and audited identity
rebind chains. A valid base plus valid amendments/revocations/rebinds remains
`cohort_integrity_valid: true`; amendment and rebind counts/digests are reported
without identities.

## Prelogin limitation

A prelogin grant is intentionally bound only to the exact live Clerk issuer,
subject, and verified primary email. It cannot safely contain a Doctor ID
because no Doctor exists yet, and it does not establish practice membership.
On first authenticated request the normal Doctor upsert creates the shell, and
the existing runtime grant check re-verifies Clerk before allowing the pilot
bypass. If a Doctor or conflicting email owner appears between dry-run and
apply, the apply fails closed and must be reviewed again.
