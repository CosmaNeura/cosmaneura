# Automatic Scribe activation — ADR 0029

Risk: **Tier 2** (provider identity, entitlement and cross-repository contract).
Design ratified by architecture PR 23, merge `75055344a96f2445d690512486a97817803337de`.
Implementation does not authorize promotion, migration, billing changes or rollout.

## Sequence

1. Core correspondence service and dormant additive `practice_scribe_tenant`
   expand. Reuse the reviewed provisioning checks and writer; retain the
   operator command's explicit email verification and plan confirmation.
2. Core closed setup response, never-activated cancellation status and explicit
   dual-authenticated activation route. Reads remain non-mutating.
3. Scribe receiver passthrough and activation transport, then native setup UI
   and fixed-origin onboarding handoff. Existing 401/403 recording fences stay.
4. Independent review of every final substantive implementation commit.
5. Founder selects reviewed merged Core and receiver candidates. Before any
   deployment, reconcile their SHA, digest, configuration and schema against the
   running environment. Existing source drift must be retained, assimilated or
   explicitly replaced; deploying a repository tip blindly is prohibited.
6. Founder separately approves the expand, environment promotion and a synthetic
   practice cohort. Receiver contract support precedes the new capture client.
7. Produce and verify iOS build 95 or newer from merged sources. Confirm the
   account-deletion change is merged, included and reachable before recording.
8. Successful physical-phone dry run, then a fresh synthetic account for the
   review movie. App Store Connect remains John's responsibility.

## Capability and cohort

`SCRIBE_AUTOMATIC_ACTIVATION_ENABLED` defaults to false.
`SCRIBE_ACTIVATION_PRACTICE_REFS` defaults to an empty comma-separated list of
Core practice TypeIDs. Both the capability and an exact practice selection are
required. Native workload authority must also be enabled. A disabled private
activation facade returns 503. A disabled authority or an unselected practice
returns a denial (403), never an activation invitation.
The capture processor's separate tenant admission gates still apply.

The private native POST `/service/v1/agent/scribe/activation` accepts only
`action: "activate"` and a canonical, nonzero UUID `request_nonce`, with a
canonical nonzero UUID `Idempotency-Key`. Both verified Clerk human and admitted
native workload authentication are required. Browser cookies, view-as state,
mount headers and body-supplied identities are rejected. A successful response
contains only version, action, nonce and `state: "active"`; it grants no recording
authority. A fresh provider-context read must succeed before opening the app.

Provider-context and activation setup responses are bounded 409 problem JSON:
exactly `type: "scribe_account_setup_required"`, `status: 409`, `setup` (practice
or activation) and the request nonce. The facade preserves no-store and record
version 1. This private route remains excluded from public OpenAPI generation.
Cancellation status returns 404 only for a verified, never-activated principal;
preview/request remain denied. Deleted, revoked, ambiguous, rebound historical
and cancelled principals remain denied, rather than receiving setup invitations.
Existing own cancellation status and idempotent retry receipts remain readable
after seat or grant loss; this preserves prior receipt behavior and never restores
processing access. Missing receipts still apply every account tombstone fence.

Existing grants, BAA records, membership, Stripe state and capture admission
are neither granted nor rewritten. An active grant supplies entitlement only
through the existing decision; automatic activation also requires an authorized
active membership and an in-force practice BAA. Any revoked grant or provider
link blocks automatic activation, including a move to another practice.

## Expand and recovery

Revision `e78845cecca6` expands from `ddd6261fa36f`. It creates one unique,
nonzero Scribe UUID per practice with an immutable mapping. All historical links,
including revoked links, are backfilled only when the practice's links agree and no UUID is shared
between practices. Conflict errors contain aggregate counts, never identities.
The same guard works in online migration execution and generated rollout SQL.

Rehearse on a disposable synthetic database. Confirm agreeing links preserve
their UUID, conflicting links abort before table creation, and both reassignment
and deletion are rejected. No production exports or patient fixtures are needed.

Recovery closes the activation capability/cohort and restores the prior reviewed
application images. Leave the additive mapping dormant. Downgrade deliberately
refuses to remove it; a future contract cleanup requires separate approval.
Recovery must not remove cancellation, revocation or legal evidence.

## Canonical staging configuration preparation

The staging controller accepts three additional optional, complete public
groups: native workload/context/visit controls; automatic activation and its
exact practice TypeID cohort; cancellation intake, migrated-ledger readiness
and its reviewed policy. Missing groups remove inherited values before the
reviewed overlay; they never supply enabled defaults. Historical runtime plans
remain valid. Partial groups, noncanonical Boolean values, wildcard/duplicate
or oversized cohorts, incomplete native prerequisites and invalid policy
values are refused with content-free errors.

Native controls, activation and cancellation intake/policy are API-only.
`SCRIBE_CANCELLATION_LEDGER_READY` is shared by API, worker and scheduler: the
existing Doctor fence returns no denial while it is false, so API-only readiness
would leave processing unfenced. After the cancellation ledger is migrated,
retain readiness on every workload even when cancellation intake is closed.
Closing intake neither removes receipts nor restores processing access.

The pure pre-mutation guard receives only an explicit public projection. It
retains the existing staging Entra tenant binding and exact Clerk issuer,
checks the native authority token timing, and refuses native caller overlap
with the original Sovereign tool callers. Native authentication selects the
same native singleton for both policy checks and compares verified client IDs
in `authenticate_native_workload`; it does not require equality with legacy
capture caller/audience settings. No application imports, credential reads or
new runtime identities are introduced by the controller.

This patch supplies configuration ownership and validation, not an enabled
cohort, migration, billing exemption, credential/configuration change or release
authorization. Reconcile actual source/image/configuration/schema drift, rehearse
and separately approve the existing expand through `e78845cecca6`, then bind
the full effective configuration fingerprint and exact per-workload recovery
revisions. Existing candidate/PR/controller provenance, schema checks and founder
dispatch remain mandatory. Changed controller bytes require a fresh reviewed
dispatch; the audited dependency manifest binds the revised controls.

Retain the separate staging Scribe receiver topology. The canonical Core image
does not include the separately assembled Scribe runtime; bundle controls remain
outside the admitted public groups. Receiver support must be reviewed and
promoted before the staging iOS client can qualify the physical-phone flow.
Actions remains disabled pending a separate owner decision; preparation does
not invoke or rerun a workflow.

Recovery closes automatic activation/cohort and cancellation intake, restores
the prior reviewed images/configuration through the retained-revision recovery,
and keeps ledger readiness and additive structures intact. Reverting the
controller's ownership support is not an immediate rollback over a configured
native environment: strict preservation would refuse its owned runtime names.
That requires an explicit reconciliation or a reviewed fix-forward.

## Qualification and review evidence

Owned PostgreSQL qualification covers fresh/incomplete setup with no writes,
grandfathered entitlement without billing changes, approved second members,
preserved personal authority namespaces, reused emails without adoption,
concurrent activation, lost-reply retry, existing reviewed links, cancellation,
deleted/revoked/inactive accounts, practice moves after link revocation and
best-effort post-commit events. Each successful first activation writes one
metadata-only audit event. Events never undo their already-committed domain work.

Record exact commit, local commands/results, diff budget, migration rehearsal
and independent reviewer findings in the implementation PR. Independent review
is pending until a non-implementer reviews the final substantive commit; local
passes do not substitute for that review or GitHub approval.


## Dark Core setup guidance follow-on — 2026-11-05

Tier 2 source-only follow-on. Owner: John. This page is not activation and
changes no membership, attestation, BAA, grant, billing, cohort, schema or native
contract. It is prepared separately from the selected f356/d7bf staging release;
no deployment or activation occurs with this source PR.

`/scribe/setup` requires the existing signed-in Core client, refuses unknown or
active view-as before reading existing caller-bound practice metadata, and
returns fixed guidance without exposing server error details. It offers the
existing ordinary enrollment or exact current owner-only BAA flow, or a clear
owner/support review when an existing membership lacks lawful enrollment.
Revoked/ended members never get either form. Nothing automatically attests,
reenrolls, adopts another actor, restores Scribe cancellation or creates a link.

The explicit Core path `/onboarding?scribe=setup` bypasses only the completed-user
navigation redirect, uses the existing enrollment/BAA forms, and ends with
instructions to return to native Check access instead of optional onboarding.
Plain `/onboarding` keeps its current behavior. Query mode grants no authority;
all existing server checks remain authoritative. Already-linked grandfathered
read discovery and native processing are unchanged.

Build97 opens only plain `/onboarding`. This additive Core route is reachable
through an explicit Core URL/link without rebuilding iOS; it does not pretend
that97 automatically selects the new mode. Wider native integration, live
rollout, genuine missing-enrollment reconciliation and broader practice/capture
admission remain separately approved work.

Independent final-push review and relevant local tests must pass before merge.
Before deploying a later Core candidate, prove signed-out/view-as refusal,
normal onboarding redirect, explicit completed-user guidance, legitimate owner
BAA acceptance, blocked existing-member attestation cases, unchanged linked
access and return-to-native identity/revision checks using synthetic accounts.
Source recovery is revert this follow-on, retaining the prior reviewed candidate.
Runtime recovery uses the qualified prior Core artifact through the approved
release process; do not remove correspondence or additive structures. This source
preparation does not qualify new CREATE/legacy cohorts or production.
