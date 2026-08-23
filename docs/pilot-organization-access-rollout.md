# Pilot organization access rollout

Status: operator runbook; not authorization to change production.

This runbook activates roster-based pilot access for the two approved provider
organizations. MyCatholicDoctor (MCD) remains one provider organization above
two separate practice tenants; Fiat receives one new practice tenant through
the audited bootstrap below. It deliberately keeps organization authorization
separate from personal BAA attestation: access requires an exact roster entry,
a Clerk primary email whose verification status is explicitly `verified`, the
current organization code, an in-force negotiated BAA, and an active billing
entitlement on the target practice.

This rollout does not merge practices, move patients, appointments, notes, or
other clinical records, or create cross-practice clinical access. Each roster
identity is assigned to exactly one target practice.

Never paste roster addresses, access codes, credentials, document contents, or
local file locations into this file, a ticket, CI output, or a shared log.

## Accepted source facts

| Artifact | SHA-256 | Effective date / scope |
| --- | --- | --- |
| MyCatholicDoctor fully executed MSA with BAA exhibit | `81f8e4bce9818050c0d7fe78c8e441677c3ab97f2111d45699d481c7163b8b16` | `2026-02-23` |
| Fiat BAA | `4cd7f20f63a61cc6fbc74ce4e58e9fac6653836cb9bff81eabb300b5321ce4f4` | Executed and in force as of `2026-08-22`, recorded prospectively by explicit operator direction; do not infer an earlier date from the source PDF |
| Original account export | `9aef6c23b4f571417d963dd5a60a2b45f79ec26264d236d30fea895a645b3733` | 47 rows; 34 target identities; input only and never imported directly |
| Sanitized MyCatholicDoctor roster v1 | `6c88b50619a66cd0c344a6f08cc2e182c976728b01d7ab38d38a93b73dfd0549` | 31 exact approved identities; source list only, not the multi-practice activation artifact |
| Sanitized Fiat roster v1 | `3204e7fd8264da1c4db7a7bb4ba9b5fad7dc4ddce75b5124249228880f4f6205` | 3 exact approved identities |

The original account export contains fields outside the roster contract,
including authentication-material columns. The application must receive only
sanitized manifests. Fiat uses schema version 1. MCD activation requires a new
schema-version-2 manifest that maps every approved address to one explicit
`target_practice_id`; its new raw-file digest must be reviewed and recorded at
dry-run. The same dry-run also emits a canonical plan digest that must be
recorded and confirmed separately. Never commit any manifest or the original
export.

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
- Every target practice ID, legal name, and exact organization domain has been
  verified read-only. Administrative changes use stable practice IDs; keep
  legacy join codes out of shared output.
- MCD's two existing practices have been identified and will both remain in
  place under one organization. Their current patients, appointments, notes,
  members, owners, and other clinical records will not be moved.
- Fiat's exact-domain practice count is zero before bootstrap. Caroline Hartley
  is the explicitly approved Fiat practice owner, her active Doctor TypeID has
  been verified, and her privately confirmed live Clerk primary email is an
  exact member of the Fiat roster. Do not put her email in a command argument,
  this runbook, a ticket, or ordinary logs.
- An authorized operator can retrieve each signed agreement and independently
  reproduce the SHA-256 above. File relocation is not required.
- Each sanitized roster is a regular, non-symlink file no larger than 1 MiB and
  mode `0600`. Fiat schema version 1 has exactly `schema_version` and `members`,
  with only `email` in each member. MCD schema version 2 additionally has exactly
  `organization_slug` and `email_domain` at the top level, and every member has
  exactly `email` and `target_practice_id`. No role or extra field is allowed.
  All addresses are normalized, unique, and in the exact organization domain;
  addresses on subdomains are not organization roster members.
- The source-roster hashes and expected counts above are reproduced immediately
  before the change window. The derived MCD schema-version-2 hash is separately
  captured from the final reviewed dry-run; do not substitute the v1 hash. The
  MCD configuration-plan hash from that exact dry-run is captured separately
  and must also match at apply.
- Every target practice has no open Stripe Checkout Session and no nonterminal
  Stripe subscription. A billing exemption grants access but does not cancel a
  Stripe charge; stop for an explicit billing decision if either exists.
- The roster-to-active-member comparison below has no unresolved exclusions.
  In particular, all 29 MCD identities that do not currently have a practice
  membership have an explicitly reviewed target-practice assignment. Any
  unresolved placement blocks activation. Every active, non-deleted application
  account on the exact MCD domain must appear in the v2 manifest, including an
  account without a current practice membership; omission blocks activation.
- Clerk and Redis health checks are green. New roster claims fail closed when
  either identity verification or the online-attempt limiter is unavailable.
- Stable, non-PII operator/change references and reasons have been approved.
- A secure destination for the generated organization code is ready. The
  destination file must not already exist.
- One operator runs one organization at a time in a low-traffic window, with a
  second reviewer watching the aggregate preflight and command results.

## Pre-cutover roster versus active-member comparison

Perform this comparison in the controlled operator environment before linking
any practice to an organization. Do not use `list-pilot-doctors` for this step,
because it prints identities.

1. Read the sanitized manifest in memory and normalize each address to trimmed
   lowercase.
2. Read non-deleted provider accounts for the exact organization domain,
   including legacy onboarded accounts with no `practice_member`. Separately
   identify current, non-revoked members of every intended target practice and
   any rostered account attached outside that intended set.
3. For MCD, build the private schema-version-2 artifact. Keep each existing
   member, including each existing owner, mapped to that member's current
   practice. Assign every remaining identity to one reviewed target practice.
   Do not infer placement from name, login history, patient data, or load
   balancing. The currently unresolved 29 placements must be decided before
   the command can run.
4. Compare exact normalized addresses in memory. Do not write or print the
   sets. Record only these aggregates:

   | Measure | Required result |
   | --- | --- |
   | Manifest count | MCD: 31; Fiat: 3 |
   | Duplicate or malformed manifest entries | 0 |
   | Wrong-domain manifest entries | 0 |
   | Non-deleted domain accounts absent from the approved roster | 0, or an explicit keep/offboard decision |
   | Target-practice members absent from the approved roster | 0 |
   | Rostered accounts attached outside the intended practice set | 0 |
   | Existing MCD members whose v2 target differs from current practice | 0 |
   | MCD identities without an explicit valid target | 0 (currently 29 decisions are required) |
   | Approved roster identities with no account yet | Informational only |
   | Rostered legacy accounts with no membership yet | Informational; they will create membership on claim |
   | Exact target-practice overlap | Reconcile with the current member count |

5. Optionally retain SHA-256 values of the two sorted normalized sets as
   evidence, but never retain the underlying addresses in the change record.

If an active account or member is absent from the approved roster, stop. Obtain
an explicit decision to add that identity to the approved roster or offboard
it. If a rostered account belongs outside the intended practice set, or an MCD
target disagrees with an existing membership, stop and resolve the ownership
conflict. Never move an existing member or clinical record as an incidental
effect of this rollout. Once a practice becomes organization-managed, a
historical workforce attestation no longer authorizes a non-rostered member.

## Command order

Run commands from the exact deployed source revision with the production
environment selected through the normal protected operator mechanism. Keep
shell tracing disabled. Values represented by shell variables below must be
resolved privately and must not be echoed.

Complete Fiat's bootstrap only if its exact-domain practice count remains zero.
Then record the already-approved BAA and exemption on every target practice
before configuring either organization. Complete one organization's
configuration and roster activation before starting the other.

### 0. Bootstrap the Fiat practice only

This step applies only to Fiat. Do not use it for MCD or for any domain that
already has a practice. It creates a closed practice shell plus one existing,
unattested owner membership. Caroline Hartley is the explicitly approved owner;
the other two approved Fiat providers remain workforce members when they claim.
The command prompts privately for Caroline's exact account email and verifies
it against both the private roster and her current explicitly verified Clerk
primary email. Do not place the address on the command line.

First dry-run the exact private Fiat roster and approved owner Doctor TypeID:

```bash
python -m app.cli bootstrap-provider-practice \
  --manifest "$FIAT_ROSTER_MANIFEST" \
  --owner-doctor-id "$FIAT_OWNER_DOCTOR_ID" \
  --legal-name "$FIAT_PRACTICE_LEGAL_NAME" \
  --email-domain "$FIAT_DOMAIN" \
  --bootstrap-ref "$FIAT_BOOTSTRAP_REF" \
  --operator-ref "$CHANGE_REF" \
  --reason "$FIAT_BOOTSTRAP_REASON"
```

Require `members=3`, the accepted Fiat roster digest, and a new
`plan_sha256`. The dry-run must confirm that the exact-domain practice count is
zero, the owner has no existing practice membership, and the owner is one of
the three exact roster identities. It writes nothing and prints no email or
join code.

Apply only the artifact just reviewed:

```bash
python -m app.cli bootstrap-provider-practice \
  --manifest "$FIAT_ROSTER_MANIFEST" \
  --owner-doctor-id "$FIAT_OWNER_DOCTOR_ID" \
  --legal-name "$FIAT_PRACTICE_LEGAL_NAME" \
  --email-domain "$FIAT_DOMAIN" \
  --bootstrap-ref "$FIAT_BOOTSTRAP_REF" \
  --operator-ref "$CHANGE_REF" \
  --reason "$FIAT_BOOTSTRAP_REASON" \
  --confirm-roster-sha256 "$FIAT_ROSTER_SHA256" \
  --confirm-plan-sha256 "$FIAT_BOOTSTRAP_PLAN_SHA256" \
  --apply
```

Require one practice ID, one owner-member ID, one append-only bootstrap audit
ID, and both confirmation hashes to match the dry-run. Preserve the returned
practice ID for all subsequent administrative commands. An identical retry is
idempotent; a reused reference with a changed plan must fail.

Bootstrap does **not** record or execute a BAA, grant a billing entitlement,
create an organization, import roster authorization, call Stripe, or authorize
PHI access. Fiat's supplied organizational BAA is already executed and in
force; the next step records that existing instrument in the platform rather
than asking anyone to execute a new agreement. All three approved Fiat users
are grandfathered for BAA and billing, but they still must enroll through their
exact approved email and the shared organization code.

### 1. Record each existing negotiated BAA

`set-negotiated-baa` is an audited mutating command and does not have a dry-run
mode. Run it once per target practice, only after the existing document hash
and current practice state have been independently verified. This command
records an instrument already in force; it does not create, amend, or obtain a
new BAA. Run it for the new Fiat practice and separately for both existing MCD
practices.

```bash
python -m app.cli set-negotiated-baa \
  --practice-id "$PRACTICE_ID" \
  --reference "$STABLE_BAA_REFERENCE" \
  --document-sha256 "$BAA_SHA256" \
  --effective "$BAA_EFFECTIVE_DATE" \
  --operator-ref "$CHANGE_REF" \
  --reason "$BAA_REASON"
```

Use the MCD or Fiat hash and date from the accepted-source table. Fiat's date is
the accepted in-force date supplied by the operator. Require the returned
practice, effective date, and audit ID to match the intended target.
Do not continue if the BAA is future-dated, missing its stable reference, or
otherwise not in force. A historical clickwrap cannot substitute for the
negotiated instrument after organization linkage.

### 2. Activate the pilot billing exemption

`set-billing-exempt` is also immediately mutating and audited. Omit `--until`
only when the approved exemption is indefinite.

```bash
python -m app.cli set-billing-exempt \
  --practice-id "$PRACTICE_ID" \
  --operator-ref "$CHANGE_REF" \
  --reason "$BILLING_REASON"
```

Run this separately for the new Fiat practice and both MCD practices. Require
`billing_exempt=True` and retain each audit ID. This records the approved pilot
billing grandfathering, including all three Fiat providers; it does not create
a Stripe subscription or checkout session.

### 3. Configure Fiat's single-practice organization

Dry-run the organization configuration:

```bash
python -m app.cli configure-provider-organization \
  --practice-id "$FIAT_PRACTICE_ID" \
  --legal-name "$FIAT_ORGANIZATION_LEGAL_NAME" \
  --slug "$FIAT_ORGANIZATION_SLUG" \
  --email-domain "$FIAT_DOMAIN" \
  --operator-ref "$CHANGE_REF" \
  --reason "$FIAT_CONFIGURATION_REASON"
```

Require a successful dry-run for the exact practice and domain. No code is
generated during this step.

Set a restrictive umask and use a new file in an approved encrypted operator
workspace. The CLI refuses to follow a symlink or overwrite an existing file
and creates the output with mode `0600`.

```bash
umask 077
python -m app.cli configure-provider-organization \
  --practice-id "$FIAT_PRACTICE_ID" \
  --legal-name "$FIAT_ORGANIZATION_LEGAL_NAME" \
  --slug "$FIAT_ORGANIZATION_SLUG" \
  --email-domain "$FIAT_DOMAIN" \
  --operator-ref "$CHANGE_REF" \
  --reason "$FIAT_CONFIGURATION_REASON" \
  --access-code-output "$FIAT_ONE_TIME_CODE_FILE" \
  --apply
```

Require one organization ID and one audit ID. Never print or paste the output
file's contents.

If the command reports an **ambiguous outcome**, do not delete the retained
code file and do not rerun configuration. First verify the organization link,
configuration audit, and stored code version read-only. If the transaction did
not commit, securely remove the unused file before a reviewed retry with a new
output path. If it did commit, the retained file is the recovery copy; move it
through the approved secret-handling procedure. If the database result cannot
be proven, rotate only after that uncertainty has been resolved.

This command starts a short, intentional fail-closed window: the practice is
now organization-managed, its legacy join-code lookup and join path are
disabled, its join code is redacted from API responses, and old attestations do
not grant access. No roster claim can succeed until the next step finishes.
Keep the roster command prepared in the same operator session and minimize this
window. If roster validation fails, stop; leave access closed and investigate.
Do not unlink the organization or reopen legacy joining as an improvised fix.

Dry-run the sanitized Fiat roster import:

```bash
python -m app.cli import-organization-roster \
  --manifest "$FIAT_ROSTER_MANIFEST" \
  --practice-id "$FIAT_PRACTICE_ID" \
  --operator-ref "$CHANGE_REF" \
  --reason "$FIAT_ROSTER_REASON" \
  --source-ref "$FIAT_ROSTER_REFERENCE"
```

Require exactly three members and the accepted Fiat roster SHA-256. The command
must not print addresses. The in-force negotiated BAA and active billing
exemption are checked again under the service transaction.

Apply the exact dry-run artifact:

```bash
python -m app.cli import-organization-roster \
  --manifest "$FIAT_ROSTER_MANIFEST" \
  --practice-id "$FIAT_PRACTICE_ID" \
  --operator-ref "$CHANGE_REF" \
  --reason "$FIAT_ROSTER_REASON" \
  --source-ref "$FIAT_ROSTER_REFERENCE" \
  --confirm-sha256 "$FIAT_ROSTER_SHA256" \
  --apply
```

Require `created + unchanged=3` and require the printed digest to match the
dry-run. An identical retry is a no-op and must not add another source-import
audit; a genuinely different source digest gets one new append-only import
audit. Caroline's bootstrap membership remains `owner`: import and claim attach
the roster authorization to that existing membership without demoting its role.
The other two providers receive normal workforce memberships when they claim.

### 4. Atomically configure MCD's two-practice organization and roster

MCD must use one private schema-version-2 manifest with this exact shape and no
extra fields:

```json
{
  "schema_version": 2,
  "organization_slug": "<approved-slug>",
  "email_domain": "<approved-domain>",
  "members": [
    {
      "email": "<private-normalized-address>",
      "target_practice_id": "practice_<reviewed-target>"
    }
  ]
}
```

The manifest must contain all 31 approved identities exactly once. Existing
members and owners must point to their current practice. All 29 remaining
placements require an explicit approved mapping; an unset, unknown, duplicate,
or conflicting target blocks the entire operation.

Dry-run the combined operation:

```bash
python -m app.cli configure-provider-organization-with-roster \
  --manifest "$MCD_ROSTER_V2_MANIFEST" \
  --legal-name "$MCD_ORGANIZATION_LEGAL_NAME" \
  --operator-ref "$CHANGE_REF" \
  --reason "$MCD_CONFIGURATION_REASON" \
  --source-ref "$MCD_ROSTER_REFERENCE"
```

Require exactly two reviewed target practice IDs, 31 mapped members, the
expected per-practice counts, the new raw `roster_sha256`, and a distinct
canonical `plan_sha256`. Output must not contain addresses. The plan digest
binds the normalized organization slug and domain, every normalized
email-to-practice assignment, legal name, operator reference, reason, source
reference, raw roster digest, and the fixed active-status/access-code-version
configuration. The dry-run must verify both practices are unlinked, have the
exact organization domain, have an in-force recorded BAA and active billing
exemption, have no unresolved Stripe state, and contain no existing member
whose manifest target differs from current membership. Require
`assignments_by_practice` to match the reviewed mapping and confirm that no
active, non-deleted exact-domain account was omitted.

Apply only that exact raw artifact, writing the one organization-wide code to a
new private file:

```bash
umask 077
python -m app.cli configure-provider-organization-with-roster \
  --manifest "$MCD_ROSTER_V2_MANIFEST" \
  --legal-name "$MCD_ORGANIZATION_LEGAL_NAME" \
  --operator-ref "$CHANGE_REF" \
  --reason "$MCD_CONFIGURATION_REASON" \
  --source-ref "$MCD_ROSTER_REFERENCE" \
  --confirm-roster-sha256 "$MCD_ROSTER_V2_SHA256" \
  --confirm-plan-sha256 "$MCD_CONFIGURATION_PLAN_SHA256" \
  --access-code-output "$MCD_ONE_TIME_CODE_FILE" \
  --apply
```

The command atomically creates one organization, links both existing practices,
imports all 31 targeted approvals, and writes the required append-only audit
evidence. Every `provider_organization_configured` audit for the two linked
practices must store the confirmed plan digest as `evidence_sha256`. Any failed
invariant must roll back the organization, both practice links, every roster
approval, and every database audit in the operation. Do not fall back to
separate single-practice commands or attempt to repair a partial state manually.
Require one organization ID, both practice IDs, the per-practice
`created_by_practice` counts, aggregate created/unchanged counts, audit IDs, and
the exact confirmed roster and plan SHA-256 values. The service independently
recomputes the canonical plan before writing and records it on each linked
practice's configuration audit. A changed manifest assignment or
mutation-affecting command argument requires a new dry-run and plan review;
never reuse a stale plan hash.
After a reported success, verify committed state read-only rather than applying
again.
After an ambiguous response, reconcile the exact organization, practice-link,
approval, audit counts, stored code version, and confirmed plan evidence before
deciding whether any retry or rotation is safe. The CLI intentionally retains
the private code file for this recovery check; do not delete it until committed
state is known. If no mutation committed, securely remove the unused file before
a reviewed retry with a new output path.

## Downstream implications of the selected topology

- MCD has one organization identity, status, domain, and enrollment code above
  two independent practice tenants. The code is therefore shared across both
  MCD practices; rotating it or suspending the organization affects future
  enrollment for both. Already-claimed members require explicit revocation.
- Authorization remains practice-specific. The v2 manifest routes each address
  to exactly one practice, and the active-approval constraint does not support
  one identity simultaneously belonging to both. A future cross-practice user
  requirement needs a separately reviewed authorization-model change.
- Existing MCD members and owners remain in their current practice with their
  current role. Roster claim attaches organization authorization; it does not
  transfer membership, change ownership, or copy data. The same preservation
  rule keeps Caroline Hartley's Fiat membership at `owner` when she claims.
- The negotiated-BAA record, billing exemption or future Stripe subscription,
  entitlement evaluation, membership, roster import audit, access-grant audit,
  and offboarding evidence remain per practice. MCD therefore requires each of
  its two practices to stay independently in force and entitled even though
  organization identity and code handling are shared.
- Patient, appointment, note, recording, transcript, and other clinical rows
  remain attached to their existing practice. This rollout creates no combined
  clinical view and must not be used to move or reconcile records between MCD
  practices.
- The bootstrap command intentionally creates a temporarily closed Fiat shell.
  Its owner role is administrative structure, not authorization. Until the
  existing BAA and billing exemption are recorded, the organization is linked,
  the exact roster is imported, and the code is claimed, neither the owner nor
  the other two grandfathered providers receives PHI access.

## Organization-code handling

The code is a shared organization-wide enrollment factor, not authorization by
itself. Every claim still requires an exact active roster row and the matching
explicitly verified Clerk primary email. MCD uses one code for both practices;
the target comes only from the reviewed v2 mapping, never from the code.

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
- If configuration or rotation reports an ambiguous outcome, the CLI retains
  the newly written `0600` file because the database commit may have succeeded.
  Keep it private, verify the organization link/audits/code version read-only,
  and only then decide whether the file is active, unused and removable, or a
  further reviewed rotation is required. Never blindly rerun the command.
- Online claims allow eight attempts per authenticated account in a rolling
  15-minute window; the ninth is rejected with 429. The counter always expires.
  Verify this automatically or in staging, not by exhausting attempts in
  production.

## Smoke tests

Complete the automated suite and staging smoke before production data changes.
Capture aggregate per-practice clinical row counts before the MCD operation.
After each production organization is configured and imported, test Fiat and
each MCD target practice with an approved provider while recording only
timestamps, statuses, IDs, counts, and hashes:

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
   Verify that Caroline Hartley's Fiat membership is still `owner`, the other
   Fiat claim is `workforce`, each tested MCD identity reaches only its mapped
   `target_practice_id`, and both pre-existing MCD owners remain owners of their
   original practices.
6. Confirm each organization practice's `BaaAcceptance` count did not increase.
   Personal clickwrap submission by an organization-linked owner must return
   409.
7. Confirm legacy practice-code lookup returns no practice and legacy create or
   join returns 409 for any identity reserved by the organization roster. This
   guard remains closed if Clerk is temporarily unavailable; the provider can
   retry the organization flow after identity verification recovers.
8. Sign out and back in. Require direct dashboard access and intact existing
   application data.
9. Confirm Fiat's bootstrap, configuration, import, access-grant, BAA, and
   billing audit rows exist once with the expected non-PII references and
   hashes. For MCD, confirm organization linkage and roster-import evidence is
   present for both practices and the combined source digest is exact. Retain
   the reviewed MCD roster and configuration-plan digests with the change
   evidence. Confirm logs and analytics contain no roster address or access
   code.
10. Recheck MCD's aggregate patient, appointment, note, recording, transcript,
    and membership counts per practice. Require no clinical row or existing
    member to have changed practice; only the reviewed organization linkage,
    approvals, and audits may be new.

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
7. Replaying a roster that contains the revoked organization/practice/email
   tuple must fail and must not create a replacement active approval. The
   retained revoked row is an authorization tombstone. Do not edit or delete it;
   reactivation requires a separately reviewed explicit reauthorization
   workflow, which is not included in this release.

Offboarding an owner is refused unless the same practice retains another active
owner whose live, non-deleted account email still exactly matches that owner's
roster approval and organization domain. Correct stale identity data or transfer
ownership before retrying; do not bypass this safeguard.

For a known claimed provider, `offboard-practice-member` is also available, but
its mandatory email confirmation is a command-line option. Prefer the
interactive roster-revocation command so an address does not enter shell
history.

## Suspension and rollback

- Before `bootstrap-provider-practice --apply`, aborting has no effect. After a
  successful Fiat bootstrap, do not delete or recreate the practice to recover
  from a later step: it is deliberately closed, audited, and idempotently
  reusable. Correct the failed prerequisite and roll forward.
- Before either organization-configuration apply, aborting has no organization
  effect. A failed MCD combined apply must leave no organization, practice link,
  approval, or database audit from that attempt. Verify all four aggregate
  counts are unchanged before retrying. A known pre-commit validation rejection
  removes its unused code file automatically. An ambiguous failure retains the
  file; verify database state first, then securely remove it only if the
  transaction is proven not to have committed.
- Between Fiat configuration and Fiat roster import, the system is
  intentionally closed. Prefer a prompt roll-forward after correcting
  validation. Never bypass the roster, fabricate an attestation, delete audit
  evidence, unlink the organization, or reopen legacy joining as an improvised
  fix.
- After successful MCD activation, do not detach one practice to simulate a
  rollback. That would split shared authorization state from its audit trail.
  Keep both practice tenants and all clinical records intact and use suspension,
  code rotation, or exact-identity revocation as appropriate.
- To stop future enrollment without disabling current rostered members, rotate
  the organization code and withhold the replacement.
- To disable one claimed or unclaimed identity, use the revocation flow above.
- To disable all roster-derived access, set the provider organization's status
  to `suspended` only through a reviewed, recorded administrative database
  change. There is no general-purpose suspension CLI in this release. Verify
  the exact organization ID and affected row count before commit. Suspension
  is organization-wide, so MCD suspension affects both practices. It must leave
  practices, memberships, approvals, audits, and clinical data in place.
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
