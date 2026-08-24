# Provider account closure runbook

`close-provider-account` is the administrative path for a verified provider
account-closure request. It disables platform access while preserving the
records that CosmaNeura must retain.

## Safety boundary

The command:

- soft-disables the existing `Doctor` row and revokes its practice membership;
- revokes any active pilot grandfather grant and linked organization-roster
  approval;
- revokes the provider's governed authority identity, membership, and active
  mount sessions when that authority binding exists;
- records append-only practice and authority audit evidence; and
- applies the existing seven-calendar-year retention hold to membership and
  access-grant evidence.

It does **not** delete or alter the provider's Clerk identity, patients,
appointments, notes, transcripts, BAA acceptance, practice, or historical
audit/recovery records. Do not manually delete those rows as a substitute for
this workflow.

An owner cannot be closed while another active practice member remains;
ownership must first be transferred explicitly. A sole-member solo owner and a
non-owner member are supported. A non-solo sole owner requires a separate,
reviewed practice wind-down because provider closure alone does not terminate
the covered entity or its billing.

## Dry run and apply

Use stable internal identifiers and a durable ticket/change reference. Omit
`--confirm-email` to enter the address in a hidden prompt instead of shell
history.

```console
python -m app.cli close-provider-account \
  --doctor-id doctor_... \
  --operator-ref change:... \
  --reason "Verified provider account closure request"
```

Review the practice, membership role, revocation counts, Stripe follow-up flag,
and `plan_sha256`. Then repeat the exact inputs with the printed digest:

```console
python -m app.cli close-provider-account \
  --doctor-id doctor_... \
  --operator-ref change:... \
  --reason "Verified provider account closure request" \
  --confirm-plan-sha256 <dry-run-digest> \
  --apply
```

Any relevant state change between these invocations changes the plan or causes
a fail-closed refusal. An exact retry returns `already applied` and does not
create a second receipt.

## Required follow-up

The command intentionally performs no external deletion. Keep the Clerk user
for the retention/recovery record unless a separately reviewed identity action
is approved. If `stripe_follow_up=true`, review the practice's Stripe
subscription after closure: workforce closure queues the platform's seat-count
reconciliation, while closing a sole owner does not automatically cancel the
practice subscription or customer. Record that separate billing decision on
the same ticket.
