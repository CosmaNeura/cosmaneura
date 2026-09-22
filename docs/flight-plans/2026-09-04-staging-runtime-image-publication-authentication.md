# Flight plan: dormant runtime-image publication authentication

## Outcome and trust chain

- Tier **2** adds a dormant receiver for one immutable runtime image using only sealed policy and injected one-shot registry/verifier adapters.
- A pinned GitHub-only root and Sigstore v0.3 bundle bind one SLSA subject to the exact digest-requested ACR manifest bytes; tags, listings, `oci://`, and JSON reserialization refuse.
- Only bounded distributable OCI layer descriptors are allowed; external URLs refuse. Exact config bytes bind linux/amd64, source/revision, and GitHub run ID/attempt.
- One certificate, DSSE signature, and RFC3161 timestamp are required. Only after offline verification may the trusted clock mint `published_at`, the image authority, and its sealed 27-field receipt.

## Containment and remaining gates

- Registry transport remains outside the required no-network verifier namespace; executor containment and current Key Vault root ingress do not authorize the future publisher.
- Publisher-containment and independent build-provenance proofs remain false, the receiver rejects either claim, and all 25 activation gates remain dormant.
- No concrete registry client, credential, sender, workflow, infrastructure, artifact, or live mutation is included; future work must prove byte-preserving publication/readback and same-run build provenance.
- Hold after offline tests, lint/type/compile/hash checks, budget audit, independent review, and protected CI. Recovery is a code-only revert.
