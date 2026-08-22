# Pilot organization access rollout

Status: operator runbook; not authorization to change production.

This runbook activates roster-based pilot access for the two approved provider
organizations. It deliberately keeps organization authorization separate from
personal BAA attestation: access requires an exact roster entry, a Clerk
primary email whose verification status is explicitly `verified`, the current
organization code, an in-force negotiated BAA, and an active billing
entitlement.

Never paste roster addresses, access codes, credentials, document contents, or
local file locations into this file, a ticket, CI output, or a shared log.

## Accepted source facts

| Artifact | SHA-256 | Effective date / scope |
| --- | --- | --- |
| MyCatholicDoctor fully executed MSA with BAA exhibit | `81f8e4bce9818050c0d7fe78c8e441677c3ab97f2111d45699d481c7163b8b16` | `2026-02-23` |
| Fiat BAA | `4cd7f20f63a61cc6fbc74ce4e58e9fac6653836cb9bff81eabb300b5321ce4f4` | `2026-08-22`, recorded prospectively by explicit operator direction; do not infer an earlier date from the source PDF |
| Original account export | `9aef6c23b4f571417d963dd5a60a2b45f79ec26264d236d30fea895a645b3733` | 47 rows; 34 target identities; input only and never imported directly |
| Sanitized MyCatholicDoctor roster v1 | `6c88b50619a66cd0c344a6f08cc2e182c976728b01d7ab38d38a93b73dfd0549` | 31 exact approved identities |
| Sanitized Fiat roster v1 | `3204e7fd8264da1c4db7a7bb4ba9b5fad7dc4ddce75b5124249228880f4f6205` | 3 exact approved identities |

The original account export contains fields outside the roster contract,
including authentication-material columns. The application must receive only
the sanitized schema-version-1 manifests. Never commit either manifests or the
original export.

The sanitized extraction found 34 unique target identities and no duplicates;
all 34 were marked verified in the export. That snapshot is not sufficient for
login: every claim still checks the current Clerk primary address and requires
its live verification status to equal `verified`.

## Go/no-go prerequisites

Do not begin an organization cutover until all of the following are true:

- The reviewed application commit is deployed and healthy, and the database is
  at Alembic revision `ab25569de9c6` or its reviewed successor.
- Backend and frontend revisions resolve to the intended immutable build; CI,
  migrations, and organization-access tests are green.
- The target practice ID, existing join code, legal name, and exact organization
  domain have been verified read-only. Keep join codes out of shared output.
- An authorized operator can retrieve each signed agreement and independently
  reproduce the SHA-256 above. File relocation is not required.
- Each sanitized roster is a regular, non-symlink file no larger than 1 MiB,
  mode `0600`, with exactly `schema_version` and `members`. Each member contains
  only `email`; all addresses are normalized, unique, and in the target domain.
- The roster hashes and expected counts above are reproduced immediately before
  the change window.
- The target practice has no open Stripe Checkout Session and no nonterminal
  Stripe subscription. A billing exemption grants access but does not cancel a
  Stripe charge; stop for an explicit billing decision if either exists.
- The roster-to-active-member comparison below has no unresolved exclusions.
- Clerk and Redis health checks are green. New roster claims fail closed when
  either identity verification or the online-attempt limiter is unavailable.
- Stable, non-PII operator/change references and reasons have been approved.
- A secure destination for the generated organization code is ready. The
  destination file must not already exist.
- One operator runs one organization at a time in a low-traffic window, with a
  second reviewer watching the aggregate preflight and command results.

## Pre-cutover roster versus active-member comparison

Perform this comparison in the controlled operator environment before linking
the practice to an organization. Do not use `list-pilot-doctors` for this step,
because it prints identities.

1. Read the sanitized manifest in memory and normalize each address to trimmed
   lowercase.
2. Read non-deleted provider accounts for the exact organization domain,
   including legacy onboarded accounts with no `practice_member`. Separately
   identify current, non-revoked members of the target practice and any
   rostered account already attached to a different practice.
3. Compare exact normalized addresses in memory. Do not write or print the
   sets. Record only these aggregates:

   | Measure | Required result |
   | --- | --- |
   | Manifest count | MCD: 31; Fiat: 3 |
   | Duplicate or malformed manifest entries | 0 |
   | Wrong-domain manifest entries | 0 |
   | Non-deleted domain accounts absent from the approved roster | 0, or an explicit keep/offboard decision |
   | Target-practice members absent from the approved roster | 0 |
   | Rostered accounts attached to another practice | 0 |
   | Approved roster identities with no account yet | Informational only |
   | Rostered legacy accounts with no membership yet | Informational; they will create membership on claim |
   | Exact target-practice overlap | Reconcile with the current member count |

4. Optionally retain SHA-256 values of the two sorted normalized sets as
   evidence, but never retain the underlying addresses in the change record.

If an active account or member is absent from the approved roster, stop. Obtain
an explicit decision to add that identity to the approved roster or offboard
it. If a rostered account belongs to a different practice, stop and resolve the
ownership conflict. Once the practice becomes organization-managed, a
historical workforce attestation no longer authorizes a non-rostered member.

## Command order

Run commands from the exact deployed source revision with the production
environment selected through the normal protected operator mechanism. Keep
shell tracing disabled. Values represented by shell variables below must be
resolved privately and must not be echoed.

Repeat the complete sequence for one organization before starting the other.

### 1. Record the negotiated BAA

`set-negotiated-baa` is an audited mutating command and does not have a dry-run
mode. Run it once, only after the document hash and current practice state have
been independently verified.

```bash
python -m app.cli set-negotiated-baa \
  --join-code "$PRACTICE_JOIN_CODE" \
  --reference "$STABLE_BAA_REFERENCE" \
  --document-sha256 "$BAA_SHA256" \
  --effective "$BAA_EFFECTIVE_DATE" \
  --operator-ref "$CHANGE_REF" \
  --reason "$BAA_REASON"
```

Use the MCD or Fiat hash and date from the accepted-source table. Require the
returned practice, effective date, and audit ID to match the intended target.
Do not continue if the BAA is future-dated, missing its stable reference, or
otherwise not in force. A historical clickwrap cannot substitute for the
negotiated instrument after organization linkage.

### 2. Activate the pilot billing exemption

`set-billing-exempt` is also immediately mutating and audited. Omit `--until`
only when the approved exemption is indefinite.

```bash
python -m app.cli set-billing-exempt \
  --join-code "$PRACTICE_JOIN_CODE" \
  --operator-ref "$CHANGE_REF" \
  --reason "$BILLING_REASON"
```

Require `billing_exempt=True` and retain the audit ID. Do not create a Stripe
subscription or checkout session for this cohort.

### 3. Dry-run organization configuration

```bash
python -m app.cli configure-provider-organization \
  --practice-id "$PRACTICE_ID" \
  --legal-name "$ORGANIZATION_LEGAL_NAME" \
  --slug "$ORGANIZATION_SLUG" \
  --email-domain "$ORGANIZATION_DOMAIN" \
  --operator-ref "$CHANGE_REF" \
  --reason "$CONFIGURATION_REASON"
```

Require a successful dry-run for the exact practice and domain. No code is
generated during this step.

### 4. Apply organization configuration

Set a restrictive umask and use a new file in an approved encrypted operator
workspace. The CLI refuses to follow a symlink or overwrite an existing file
and creates the output with mode `0600`.

```bash
umask 077
python -m app.cli configure-provider-organization \
  --practice-id "$PRACTICE_ID" \
  --legal-name "$ORGANIZATION_LEGAL_NAME" \
  --slug "$ORGANIZATION_SLUG" \
  --email-domain "$ORGANIZATION_DOMAIN" \
  --operator-ref "$CHANGE_REF" \
  --reason "$CONFIGURATION_REASON" \
  --access-code-output "$ONE_TIME_CODE_FILE" \
  --apply
```

Require one organization ID and one audit ID. Never print or paste the output
file's contents.

This command starts a short, intentional fail-closed window: the practice is
now organization-managed, its legacy join-code lookup and join path are
disabled, its join code is redacted from API responses, and old attestations do
not grant access. No roster claim can succeed until the next step finishes.
Keep the roster command prepared in the same operator session and minimize this
window. If roster validation fails, stop; leave access closed and investigate.
Do not unlink the organization or reopen legacy joining as an improvised fix.

### 5. Dry-run the sanitized roster import

```bash
python -m app.cli import-organization-roster \
  --manifest "$ROSTER_MANIFEST" \
  --practice-id "$PRACTICE_ID" \
  --operator-ref "$CHANGE_REF" \
  --reason "$ROSTER_REASON" \
  --source-ref "$STABLE_ROSTER_REFERENCE"
```

Require the exact expected member count and SHA-256. The command must not print
addresses. The in-force negotiated BAA and active billing exemption are checked
again under the service transaction.

### 6. Apply the exact dry-run artifact

```bash
python -m app.cli import-organization-roster \
  --manifest "$ROSTER_MANIFEST" \
  --practice-id "$PRACTICE_ID" \
  --operator-ref "$CHANGE_REF" \
  --reason "$ROSTER_REASON" \
  --source-ref "$STABLE_ROSTER_REFERENCE" \
  --confirm-sha256 "$ROSTER_SHA256" \
  --apply
```

Require `created + unchanged` to equal the expected roster count and require
the printed digest to match the dry-run. An identical retry is a no-op and must
not add another source-import audit; a genuinely different source digest gets
one new append-only import audit.

## Organization-code handling

The code is a shared enrollment factor, not authorization by itself. Every
claim still requires an exact active roster row and the matching explicitly
verified Clerk primary email.

- Treat the generated plaintext as a secret. The database stores only its
  scrypt salt and digest.
- Transfer the code once into an approved access-controlled secret item, verify
  the recipient and permissions, then remove the temporary output file using
  the approved secure-cleanup procedure.
- Distribute it only to the designated organization contact through an approved
  secure channel. Never use ordinary email, a shared ticket, application logs,
  analytics, or this repository.
- Explain that the code can enroll multiple rostered members until rotated; it
  is not a single-use code and must not be forwarded.
- If disclosure is suspected, use `rotate-organization-code` first in dry-run
  mode and then with `--access-code-output` plus `--apply`. Rotation blocks old
  code use but does not revoke members who already claimed access.
- Online claims allow eight attempts per authenticated account in a rolling
  15-minute window; the ninth is rejected with 429. The counter always expires.
  Verify this automatically or in staging, not by exhausting attempts in
  production.

## Smoke tests

Complete the automated suite and staging smoke before production data changes.
After each production organization is configured and imported, test with one
approved provider while recording only timestamps, statuses, IDs, counts, and
hashes:

1. Confirm Google/Clerk authentication resolves the expected primary email with
   verification status exactly `verified`.
2. Confirm the organization-code prompt appears without personal BAA
   certification language.
3. Make one intentionally incorrect code attempt and require the generic 403
   response. Do not repeatedly exercise the production rate limit.
4. Enter the correct code through the UI. Require a direct transition to the
   provider dashboard and successful access to `/notes`.
5. Require practice status to show `baa_accepted=true`, `entitled=true`,
   `access_authorized=true`, `authorization_basis=organization_roster`, and
   `join_code=null`. A newly created membership has `attested=false`; a
   converted existing member may retain its historical attestation, but that
   attestation is not its current authorization basis.
6. Confirm the organization practice's `BaaAcceptance` count did not increase.
   Personal clickwrap submission by an organization-linked owner must return
   409.
7. Confirm legacy practice-code lookup returns no practice and legacy create or
   join returns 409 for any identity reserved by the organization roster. This
   guard remains closed if Clerk is temporarily unavailable; the provider can
   retry the organization flow after identity verification recovers.
8. Sign out and back in. Require direct dashboard access and intact existing
   application data.
9. Confirm the import, access-grant, BAA, and billing audit rows exist once with
   the expected non-PII references and hashes. Confirm logs and analytics contain
   no roster address or access code.

Stop and suspend the rollout on any unexpected identity, duplicate membership,
missing audit, 5xx response, BAA/entitlement disagreement, or PHI/secret in
telemetry.

## Explicit revocation test

This test is mandatory in staging. In production, run it only with a controlled
non-human identity supplied for that purpose; never revoke a real pilot merely
to test the workflow.

1. Import and, for the claimed case, enroll the controlled identity.
2. Run the revocation command without `--apply`. Omit `--approved-email` so the
   CLI prompts privately and the identity does not enter shell history:

   ```bash
   python -m app.cli revoke-organization-roster-access \
     --practice-id "$PRACTICE_ID" \
     --operator-ref "$CHANGE_REF" \
     --reason "$REVOCATION_REASON"
   ```

3. Require the dry-run to report the expected `claimed` or `unclaimed` state,
   then repeat with `--apply`.
4. Require immediate denial of new authenticated/API access and new Zoom or
   Recall work. For a claimed account, require the account to be disabled and
   its membership retained. For an unclaimed approval, require no doctor or
   membership to be created. Before offboarding, check for any already-running
   request, bot, or background job and stop it through the existing incident
   procedure; the database transaction cannot cancel work already executing.
5. Require the approval, membership when present, and one append-only revocation
   audit to remain, with `access_records_retain_until`/`retain_until` set at
   least seven calendar years after revocation.
6. Repeat the dry-run and apply once. Require the same retention timestamp and
   audit ID with no duplicate audit or deletion.

For a known claimed provider, `offboard-practice-member` is also available, but
its mandatory email confirmation is a command-line option. Prefer the
interactive roster-revocation command so an address does not enter shell
history.

## Suspension and rollback

- Before `configure-provider-organization --apply`, aborting has no
  organization effect.
- Between configuration and roster import, the system is intentionally closed.
  Prefer a prompt roll-forward after correcting validation. Never bypass the
  roster, fabricate an attestation, or delete evidence.
- To stop future enrollment without disabling current rostered members, rotate
  the organization code and withhold the replacement.
- To disable one claimed or unclaimed identity, use the revocation flow above.
- To disable all roster-derived access, set the provider organization's status
  to `suspended` only through a reviewed, recorded administrative database
  change. There is no general-purpose suspension CLI in this release. Verify
  the exact organization ID and affected row count before commit. Suspension
  must leave practices, memberships, approvals, audits, and clinical data in
  place.
- Do not downgrade the migration after any live organization data exists: its
  downgrade removes the new authorization structures. Do not roll back to an
  application image that predates organization-aware gating while a practice
  remains linked. Prefer a reviewed roll-forward fix.
- Re-activation requires root-cause review, a fresh roster diff, verification
  of the BAA and entitlement, code rotation, and the full smoke test.

## Retention boundary

The seven-year hold introduced here is deliberately narrow. It covers
organization roster approvals, membership access/revocation state, and related
authorization evidence. Offboarding does not delete clinical rows, but this
feature does not declare every clinical artifact subject to a new seven-year
retention policy.

Raw transcript minimization remains separate and unchanged: raw transcript
content is removed after 14 days under the existing transcript-cleanup policy,
with its de-identified derivative handled by that policy. Never disable or
extend that minimization job to satisfy this access-evidence hold.

## Deferred document organization

Consolidating the two source agreements into one Google Drive location is an
administrative follow-up, not a rollout dependency. The cutover may proceed if
an authorized operator can retrieve each original and reproduce its accepted
hash. Use stable opaque document references in the application audit now.
After Drive consolidation, maintain a separate controlled index mapping those
references and hashes to the new Drive objects; do not edit signed PDFs or
rewrite existing append-only audit rows.
