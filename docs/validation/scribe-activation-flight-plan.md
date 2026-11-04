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
required. Native workload authority must also be enabled. A disabled capability,
authority or cohort returns a denial (403), never a setup invitation.
The capture processor's separate tenant admission gates still apply.

Existing grants, BAA records, membership, Stripe state and capture admission
are neither granted nor rewritten. An active grant supplies entitlement only
through the existing decision; automatic activation also requires an authorized
active membership and an in-force practice BAA. Any revoked grant or provider
link blocks automatic activation, including a move to another practice.

## Expand and recovery

Revision `e78845cecca6` expands from `ddd6261fa36f`. It creates one unique,
nonzero Scribe UUID per practice with an immutable mapping. Existing active links
are backfilled only when all links for a practice agree and no UUID is shared
between practices. Conflict errors contain aggregate counts, never identities.
The same guard works in online migration execution and generated rollout SQL.

Rehearse on a disposable synthetic database. Confirm agreeing links preserve
their UUID, conflicting links abort before table creation, and both reassignment
and deletion are rejected. No production exports or patient fixtures are needed.

Recovery closes the activation capability/cohort and restores the prior reviewed
application images. Leave the additive mapping dormant. Downgrade deliberately
refuses to remove it; a future contract cleanup requires separate approval.
Recovery must not remove cancellation, revocation or legal evidence.

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
