# Flight plan: dormant runtime publisher invocation binding

## Outcome and boundaries

- Tier **2** strengthens the dormant external runtime record with two signed,
  closed-world authority fields: the publisher's exact source commit SHA and
  canonical GitHub Actions run-attempt URI.
- The offline Sigstore authenticator now requires `buildSignerDigest`,
  `sourceRepositoryDigest`, and `buildConfigDigest` to equal the signed record
  SHA, and `runInvocationURI` to equal the signed record URI. The same SHA is
  passed to `gh attestation verify` as both signer and source digest.
- In scope: receiver-side policy, record validation, verifier binding, policy
  hashes/pins, adversarial tests, and documentation. Publisher implementation,
  workflow changes, infrastructure, Azure calls, restore, cutover, and live
  rehearsal are excluded.

## Trust and ordering

- The exact canonical record remains the authority. Its existing sealed proof
  already binds the record SHA, bundle SHA, trusted-root SHA, issuer, and
  workflow identity; copying the new values into that proof would be redundant
  and would create a second representation to reconcile.
- `publisher_source_sha` is the single asserted source identity for both the
  exact signing workflow and executor image build. The receiver authenticates
  the workflow's signed assertion tying `image_digest` to that SHA; it does not
  independently prove the image build. A future sender must refuse any build
  or OCI revision-label mismatch, and image provenance remains an activation
  gate.
- Runtime validation independently requires a lowercase 40-hex source SHA and
  an exact `https://github.com/CosmaNeura/cosmaneura/actions/runs/{run}/attempts/{attempt}`
  URI with canonical nonzero decimal identifiers.
- Authentication snapshots and revalidates the canonical policy and sealed job
  references before calling the injected verifier. The record authority is
  parsed from immutable bytes. Callback mutation of caller-owned policy,
  record variables, or reference objects cannot alter the authority used after
  verification.
- The signer identity pin now admits only one workflow filename directly under
  `.github/workflows/` on protected `master`; nested or lookalike paths refuse.

## Activation, rollout, and recovery

- All activation and implementation flags remain false. In particular,
  `runtime_record_publication_authority_bound` remains false: this receiver
  contract authenticates the future publisher's GitHub signing-invocation
  assertion and identity, but does not prove or authorize the Azure publisher
  invocation, its create-only Blob writes, or its Azure role.
- Hold after focused and broad offline tests, Ruff, Pyright, compile and policy
  validators, change-budget verification, independent final-head review, and
  protected PR checks.
- Recovery is a code-only revert while dormant. No schema, data, credential,
  artifact, infrastructure, workflow, or live-state mutation occurs.

## Remaining gates

- Separately implement and review immutable publisher execution authority and
  sender behavior, private DNS/public-network/RBAC/egress evidence, offline
  verifier containment, evidence/HMAC ingress, default dependency assembly,
  workflow integration, and an explicitly authorized synthetic-only rehearsal.
- Do not treat the publisher run as the launch run. Launch authorization remains
  independently bound by the broker launch record and receipt chain.
