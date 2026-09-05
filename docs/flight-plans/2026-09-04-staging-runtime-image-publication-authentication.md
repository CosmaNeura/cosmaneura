# Flight plan: dormant runtime-image publication authentication

## Outcome and trust chain

- Tier **2** adds a receiver-first, dormant authenticator for one immutable
  runtime image. It derives the repository from sealed configured policy and
  accepts only injected one-shot registry and offline-verifier adapters.
- A raw Sigstore v0.3 bundle and pinned GitHub-only root bind one SLSA subject
  SHA to the SHA-256 of the exact ACR manifest bytes. The exact digest request,
  `Docker-Content-Digest`, subject SHA, and raw manifest SHA must agree; no tag,
  listing, `oci://`, or JSON reserialization is accepted.
- The manifest must describe one OCI linux/amd64 image. Its config descriptor
  digest and size bind the exact config bytes, whose source, revision, run ID,
  and run-attempt labels must match the policy-pinned GitHub workflow
  certificate and canonical run URI.
- One certificate, DSSE signature, and RFC3161 timestamp are required; tlog
  material, self-hosted runners, wrong repository/ref/event/workflow, and
  unpinned verifier/root inputs refuse. Only after offline verification does a
  trusted UTC clock supply `published_at` and the existing image-publication
  authority is minted. A separate sealed receipt retains the evidence chain.

## Containment and remaining gates

- The registry transport is outside the publisher verifier's required
  no-network namespace. The shared verifier binary/root metadata is reused,
  but the executor invocation plan and containment evidence do not cover this
  publisher path. Current Key Vault root ingress likewise authorizes job
  identities, not a future publisher.
- Canonical publisher-containment and independent image-build-provenance proof
  flags remain false. This receiver rejects policies claiming either proof;
  labels and provenance remain assertions, and the new
  `runtime_image_publication_authenticated` gate stays false. All 25 activation
  gates remain dormant.
- No registry client, sender, upload/signing path, workflow, infrastructure,
  credential, or live action is added. A future sender must separately prove
  byte-preserving OCI publication/readback and same-run build provenance.
- Hold after offline tests, lint/type/compile checks, policy hash validation,
  budget audit, independent review, and protected PR checks. Recovery is a
  code-only revert; this slice mutates no artifact or external state.
