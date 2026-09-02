# Flight plan: add the dormant Core external-session client

## Outcome and boundaries

- Problem and measurable outcome: Core can construct and validate the Scribe v1.1 external-session
  request and response through its existing passwordless workflow client, while no existing
  application path invokes the new method.
- In scope: strict request/response models, one protocol/client method, disabled-client behavior,
  focused contract tests, and boundary documentation.
- Explicit non-goals: a staff or public route; Clerk, session, invitation, or entitlement changes;
  practice mapping; creator visibility; patient/name/DOB handling; transcript delivery or
  translation; desktop, Chrome, Zoom, or iOS integration; schema/migration work; Terraform,
  deployment, configuration, or activation.
- Affected users, services, environments, and data classes: no user or environment is affected.
  The dormant Core client gains an additive private method. Tests use generated opaque identifiers
  and synthetic strings only.

## Risk and dependencies

- Tier and reason: Tier 2 because this method sits on a consent-, tenant-, and workload-authenticated
  clinical-processing boundary, even though it is dormant and carries only opaque references.
- Irreversible or high-blast-radius decisions: none activated here. There is no call site, schema,
  configuration, credential, deployment, or traffic change.
- Dependency order: governance workflow contract v1.1 in
  `CosmaNeura/cosmaneura-architecture#12`; Scribe receiver stack through
  `CosmaNeura/cosmaneura-scribe#64`; this dormant Core method; then separate Core admission and
  creator-ownership work; transcript delivery; capture clients; and dedicated-environment
  activation.
- Receiver-before-sender rule: Scribe must merge and deploy the receiver dark before Core uses the
  method. The dormant client code may be held or merged without a call site, but it cannot be
  activated against an absent or unverified receiver.

## Current deployed state

- Review baseline: `origin/master@8fa02a71f4b880cd43f4fccc8c81c92a0d635623`.
- Live environment SHA, schema, network, and Scribe stack state were not inspected in this packet;
  no production or staging claim is made.
- Repository defaults keep the existing Scribe workflow client disabled. No current route or job
  calls the external-session method.
- Reconciliation decision: retain deployed behavior. A later activation packet must record exact
  Core and Scribe SHAs, schema revisions, workload identity, private network, and configuration.
- Previous known-good boundary: Core has only dormant consent and ordinary-session client methods;
  no end user can start this external Scribe workflow.

## Feature flag / release control

- No new user-visible flag is needed because this packet adds no call site. The existing
  `SCRIBE_WORKFLOW_ENABLED=false` default still controls construction of the shared outbound client,
  and the Scribe receiver has its own false-default external admission control.
- Enabling the ordinary Core client alone does not authorize an external session. A later user
  workflow must add its own durable grant and creator-ownership checks before calling this method.
- Removal condition: N/A; no temporary user behavior is introduced.

## Schema/data sequence

N/A. This packet adds no table, column, migration, backfill, seed, or data rewrite and deliberately
avoids a competing Alembic head while the Zoom occurrence work is in flight.

## Rollout and promotion

1. Merge the governance v1.1 contract.
2. Merge the Scribe receiver stack and deploy it dark; do not admit a practice.
3. Merge this Core client with no call site and no environment change.
4. In separate reviewed Core work, enforce the founder-issued beta grant, canonical synthetic
   practice, active practice-authorized membership, encounter access, immutable consent, and
   creator-only visibility.
5. Add the receiver-first transcript envelope and the individually accepted capture-client paths.
6. Reconcile both deployed states, then use a founder-authorized change to admit exactly one
   isolated synthetic practice and run a synthetic canary.

Abort activation on any authentication regression, practice mismatch, wrong response mode,
cross-tenant access, creator-visibility failure, duplicate session, PHI in logs or fixtures, or
unexpected deployed-state drift.

## Security and recovery

- The client reuses the existing managed identity, `.default` scope, verified practice header, and
  idempotency header. It adds no secret or browser credential flow.
- Tenant, mode, scheduling source, patient name, date of birth, and transcript content cannot enter
  the request model. Core requires Scribe's server-pinned external mode in the response.
- Scribe's opaque admission `404` maps to a bounded rejected error; remote response bodies are not
  copied into exceptions.
- Recovery before activation is to revert this client PR. After later activation, disable Scribe
  external admission first and let already admitted sessions drain; never switch to an
  unauthenticated call or abandon cleanup.
- Observation window: N/A for this dormant packet. The activation packet must define one.

## Completion

- Focused and repository checks pass on the final substantive commit.
- Independent non-implementer review identifies that exact commit and all findings are resolved or
  explicitly accepted in the PR.
- The PR remains free of routes, UI, auth, schema, infrastructure, and environment changes.
- Documentation records the v1.1 contract dependency and the exact activation blockers.
