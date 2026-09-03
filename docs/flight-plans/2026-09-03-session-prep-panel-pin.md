# Session Prep panel integration

## Outcome

Make Core serve the already reviewed Sovereign Session Prep panel bytes while
keeping the capability dark. This closes the browser-asset gap between the
merged Core producer and Sovereign consumer slices; it does not activate the
feature or authorize real provider data.

## Exact inputs

- Core base: `d543e035d968d3dccc34cd03baaef90b41a4629c`.
- Core Session Prep merge: `6cea63e59203a8730af39497a3672b3f4598ba98`.
- Sovereign corrected source candidate: `8f3b1562cb0296222278d84e09504f3252e96bbf`.
- Receiver baseline merge: `8020b745673a13875aecf32941a6711d22926045`.
- Panel SHA-256:
  `e02a761ba1943b865bb9edeb5434ffe8095f12b865f1f9b4ea402039a16c5bcb`.
- Panel SRI:
  `sha256-4Cp2G6GUO4Zbue3rVDT/6AlfErhl8fm06kAgOaFsW8s=`.

These panel bytes are the exact output of the corrected Sovereign candidate;
they are not yet claimed to be present on Sovereign `main`.

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

1. Keep prior merged panel bytes recoverable from Git history.
2. Add the exact corrected Session Prep panel bytes under their SHA-256 name.
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
- Only the dedicated Session Prep POST body carries the authorized opaque
  `context_snapshot_ref`; generic chat, URLs, DOM, and browser storage do not.
  No patient or record identifier, note/source reference or content, artifact
  provenance, or digest is sent with that request.

## Promotion and recovery

Promotion is deferred to the canonical staging workflow after its exact source
SHA, immutable image digests, recovery image, schema, synthetic-data evidence,
TLS, environment policy, and operator approvals are reconciled. Roll back this
packet by reverting its exact commit, which restores the prior
`SOVEREIGN_PANEL_*` pin and content-addressed asset.

Real-provider or PHI use remains prohibited until the separate Auth/IAM,
durable artifact/idempotency, audit/retention, source-adapter, monitoring,
incident-response, and clinical/legal governance work is complete and
explicitly approved.

## Local verification (2026-09-03)

- The Core and Sovereign request/result schemas and seven fixtures are
  byte-identical.
- The vendored file is byte-identical to Sovereign candidate `8f3b1562cb0296222278d84e09504f3252e96bbf`
  and hashes to the pinned SHA-256 above.
- Sovereign's independent panel contract harness passed against the Core-vendored
  file, including record switching, stale-response suppression, revocation,
  retry identity, four states, and browser-storage leakage checks.
- Sovereign's runtime suite passed 68 tests, including the three-call/ten-second
  polling bound and direct typed-response projection.
- Core Vitest: 43 files and 234 tests passed with synthetic public test values.
- Core TypeScript typecheck, Prettier, and ESLint passed.
