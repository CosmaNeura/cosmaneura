# Flight plan: dormant private evidence-material reader

## Outcome and boundary

- Tier **2** adds a dormant one-shot provision reader for the six exact
  versioned evidence Blobs, one exact versioned GitHub attestation Blob, and
  one exact-version Key Vault server-scope HMAC secret selected only by a
  validated launch record.
- `PrivateProvisionInputs` retains ordered immutable bytes plus the receipt,
  launch-record, and execution binding. Its seal is rechecked against the same
  canonical receipt and record before a future consumer may use the bytes.
- The reader does not load evidence policy, choose a clock, validate the bundle,
  read the already-authenticated GitHub root again, write material to disk, or
  assemble executor dependencies. Those remain later same-process work.

## Risks and controls

- A burn-on-entry lock permits one invocation even after failure and prevents
  concurrent duplicate reads. Receipt, record, selector, execution authority,
  and launch expiry against a required shared trusted UTC clock are revalidated
  before, between, and after remote stages.
- The provision UAMI obtains one Storage token and performs six evidence GETs
  in policy order plus one attestation GET. It then obtains one Vault token and
  performs one exact-version HMAC GET: ten HTTP exchanges maximum, one attempt
  each, within one 90-second monotonic deadline.
- Exact final URLs, 200 status, content length, absence of ranges/transfer
  encoding, identity encoding, media type, size, and SHA-256 are mandatory.
  The HMAC value must be canonical unpadded base64url, decode to 32–4096 bytes,
  and match the signed decoded-material digest.
- Default clients disable proxies and redirects and use only Container Apps
  managed-identity environment values. No CLI, ambient credential, listing,
  polling, retry, caller selector, or evidence-material disk path exists.
- Error codes and representations are bounded and omit tokens, URIs, secrets,
  and remote bodies.

## Release, recovery, and completion

- The runtime policy marks this reader `dormant_implemented_unintegrated` while
  `private_evidence_ingress_bound`, `private_secret_ingress_bound`, workflow,
  restore activation, and all other live flags remain false.
- Recovery is a code-only revert; this slice performs no Azure call in tests,
  provisions no infrastructure, restores no data, and changes no workflow.
- Before activation, infrastructure must prove exact evidence-container and
  Key Vault RBAC scopes, private DNS/endpoint routing, egress, and publisher
  media metadata. The dependency-assembly slice must require a fresh validated
  receipt, call `_is_bound_to`, and pass `evidence_files()` to
  `validate_bundle_snapshot_bytes` with the separately pinned evidence policy,
  the same shared trusted UTC, and launch-record digest map.
- Completion requires focused and broad tests, Ruff, formatting, Pyright,
  policy/compile/diff checks, and independent review of the final local commit.
  Publishing, merging, workflow integration, Terraform, live rehearsal,
  staging cutover, and production/PHI promotion are out of scope.
