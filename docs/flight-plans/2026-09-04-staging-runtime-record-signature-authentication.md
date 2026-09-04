# Flight plan: dormant runtime-record signature authentication

## Outcome and boundaries

- Tier **2** adds a dormant authenticator for the exact external runtime record,
  its content-addressed Sigstore v0.3 bundle, and the independently delivered
  GitHub-only trusted root.
- In scope: a strict runtime-record `gh attestation verify` profile, bounded
  one-shot execution through the existing digest-pinned Linux verifier,
  closed-world bundle/result checks, certificate/statement/time binding, a
  sealed `VerifiedExternalRuntimeRecordSignature`, tests, and documentation.
- Exact private Blob and Key Vault readers, dependency assembly, publisher,
  workflow, Azure resources, restore, cutover, and live rehearsal are excluded.

## Trust and ordering

- Validate an executable-shaped runtime policy and both sealed, identical job
  references before examining bytes. Bind exact record, bundle, and root bytes
  to their size and SHA-256 pins before one verifier call.
- The verifier profile admits only `runtime-record.json`,
  `runtime-record.sigstore.json`, and `github-trusted-root.jsonl`, with fixed
  offline flags, repository/ref, policy identity/issuer, deadline, and output
  bound. It cannot accept the evidence-attestation profile or mixed files.
- Require the GitHub-only root with no transparency logs, one certificate and
  RFC3161 timestamp, one DSSE signature, and one SLSA v1 subject whose digest is
  the exact runtime-record bytes. Require the echoed bundle and signed payload
  to match the verifier result.
- Independently parse the certificate and cross-check the exact workflow SAN,
  OIDC issuer, private repository/ref, GitHub-hosted runner, workflow-dispatch
  trigger, GitHub extension claims, coherent commit digests, and run URI.
  Verified time must use the pinned GitHub TSA, be within certificate validity,
  not precede record creation, and not exceed the trusted clock.
- Only after all checks pass may the authenticator mint the existing sealed
  signature proof and run the complete external-runtime binding validator.
  Every refusal is bounded and includes no signed or verifier diagnostic data.

## Activation, risk, and recovery

- The checked-in runtime policy remains disabled and placeholder-bound. No
  executor or workflow imports the verifier adapter; the
  public dependency factory continues to refuse unconditionally.
- A later dormant receiver-side slice binds the certificate's three source
  digests and run invocation URI to explicit signed runtime-record fields; see
  the [publisher invocation binding flight plan](2026-09-04-staging-runtime-publisher-record-binding.md).
  The actual create-only publisher remains unimplemented, so
  `runtime_record_publication_authority_bound` remains false.
- Proxy scrubbing and sealed executable bytes do not prove a no-network
  namespace. `offline_verifier_containment_proven` remains false until runtime
  confinement evidence is configured and reviewed.
- Recovery is a code-only revert while dormant. No schema, data, credential,
  infrastructure, or live-state operation occurs.

## Verification and hold points

- Cover exact arguments/files/call count, profile confusion, byte/digest
  substitutions, malformed roots/bundles/DSSE/SLSA, certificate and GitHub
  claim substitutions, time boundaries, verifier failures, bounded errors,
  and checked-in-policy early refusal.
- Run focused authentication/adapter tests, the complete staging restore and
  broker suites, Ruff, Pyright, compile checks, policy validators, diff checks,
  and the 2,000-line/50-file budget check.
- Hold after independent final-head review and protected PR checks. Later Tier-2
  slices must add private readers, default dependency assembly, immutable
  publisher authority, containment proof, and separately approved live
  rehearsal before any activation flag may change.
