# Session Prep panel integration

## Outcome

Make Core serve the already reviewed Sovereign Session Prep panel bytes while
keeping the capability dark. This closes the browser-asset gap between the
merged Core producer and Sovereign consumer slices; it does not activate the
feature or authorize real provider data.

## Exact inputs

- Core base: `e63debb1cf0ac89829afe2fc758d3e3982f07786`.
- Core Session Prep merge: `6cea63e59203a8730af39497a3672b3f4598ba98`.
- Sovereign product-slice merge: `8020b745673a13875aecf32941a6711d22926045`.
- Independently reviewed Sovereign source: `a6bde339056cbaf4dea6d51e7196a4a37f8ab452`.
- Panel SHA-256:
  `0293b507400050a59ee3fe030bd1403a07b3ba59492a69a72ea303a5da61ad72`.
- Panel SRI:
  `sha256-ApO1B0AAUKWe4/4DC9FAOgezullJKmmnLqMDpdphrXI=`.

The same panel bytes are present at Sovereign `main` commit
`92e606029d1fa3f2173f415d6b85f517204a7b63`.

## Risk and boundaries

Tier 2 by effect because this asset is hosted in a provider-facing, potentially
PHI-bearing surface. The source change is additive and recovery is immediate,
but activation is outside this packet.

This packet must not:

- add `session-prep:read` to provider grants;
- issue or expose a Session Prep context handle;
- enable Core or Sovereign Session Prep configuration;
- change Auth/IAM, staging policy, deployment workflows, infrastructure, or
  database schema;
- use real providers, records, notes, or PHI;
- deploy or promote an environment.

Core continues to pass only the appointment record identifier to the embedded
panel. The Session Prep control remains absent unless Sovereign's governed
`/welcome` response advertises it, which cannot occur with current gates off.

## Build sequence

1. Preserve the prior content-addressed panel files for rollback.
2. Add the exact reviewed Session Prep panel bytes under their SHA-256 name.
3. Update the Core source, SRI, and upstream-commit pin atomically.
4. Update the host integration expectation for the panel's fail-closed 401/403
   behavior. Token rotation remains tested by `SovereignPanelHost.test.tsx`.
5. Run the Core asset and host tests plus the independently maintained
   Sovereign runtime and browser harnesses at their exact reviewed commits.
6. Obtain independent review of the final Core commit before merge.

## Synthetic acceptance

- Core and Sovereign contract fixtures are byte-identical.
- Finalized-note selection excludes drafts, raw transcripts, deleted,
  unreviewed, future, and over-age records and enforces the five-note bound.
- Snapshot and prepared-brief replacement under an existing reference fails.
- Polling is limited to three calls and ten seconds.
- Ready, refreshing, pending, and unavailable states render distinctly.
- A record or encounter switch clears content synchronously and late responses
  cannot repaint the prior record.
- Mid-poll revocation and terminal 401/403 responses clear or suppress content.
- Record identifiers, snapshot references, provenance, digests, and browser
  storage APIs do not cross into the browser request or rendered surface.

## Promotion and recovery

Promotion is deferred to the canonical staging workflow after its exact source
SHA, immutable image digests, recovery image, schema, synthetic-data evidence,
TLS, environment policy, and operator approvals are reconciled. Roll back this
packet by restoring the prior `SOVEREIGN_PANEL_*` pin; the old content-addressed
asset remains available.

Real-provider or PHI use remains prohibited until the separate Auth/IAM,
durable artifact/idempotency, audit/retention, source-adapter, monitoring,
incident-response, and clinical/legal governance work is complete and
explicitly approved.

## Local verification (2026-09-03)

- The Core and Sovereign request/result schemas and seven fixtures are
  byte-identical.
- The vendored file is byte-identical to Sovereign merge `8020b745673a13875aecf32941a6711d22926045`
  and hashes to the pinned SHA-256 above.
- Sovereign's independent panel contract harness passed against the Core-vendored
  file, including record switching, stale-response suppression, revocation,
  bounded polling, four states, and browser-storage leakage checks.
- Core Vitest: 43 files and 233 tests passed with synthetic public test values.
- Core TypeScript typecheck, Prettier, and ESLint passed.
