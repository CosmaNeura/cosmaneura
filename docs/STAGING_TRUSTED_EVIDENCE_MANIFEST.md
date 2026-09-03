# Staging trusted-evidence manifest contract

Status: **CONTRACT VALIDATION ONLY — ACTIVATION BLOCKED**

`contracts/staging-rehearsal/v1/evidence-manifest-policy.json` defines the
closed-world v1 evidence bundle. `scripts/validate_staging_evidence_manifest.py`
is a standalone, pure local validator: it performs no provider, database,
GitHub, Azure, restore, or sanitizer operation.

## Bundle contract

The bundle directory contains exactly six owner-controlled, non-writable-by-
group-or-world regular files and no symlinks or directories:

- `evidence-manifest.json`
- `candidate.json`
- `inventory.json`
- `quiescence.json`
- `server-scope.json`
- `SHA256SUMS`

`SHA256SUMS` contains the sorted SHA-256 of the other five exact file bytes.
The manifest separately records each artifact's file SHA-256 and canonical
receipt SHA-256. Its `evidence_set_sha256` is the SHA-256 of canonical JSON for
the manifest with that one field omitted. An extra or missing file, unsafe
path, symlink, changed byte, mismatched receipt, or path escape fails closed.

Every object and nested identity structure has an exact field set. Inventory
count, rule, table-HMAC, identity-HMAC, and blob-HMAC keys are derived from the
exact pinned synthetic-data policy; empty, extra, or missing nested surfaces
fail. The validator rejects unknown fields and any caller-supplied JSON boolean. It
cross-binds all four receipts to one source SHA, candidate SHA, source Azure
resource identity, observation/expiry window, protected repository/ref,
workflow file and SHA, run ID/attempt, dispatch event, signer claims, and
versioned policy digests. The HMAC key identifier must be the supported
`staging-inventory-hmac-v1`; key material never enters the bundle.

The candidate receipt binds the verified approval PR, protected-master SHA, and
the exact schema revision required by the inventory profile to the same
source/candidate pair. The inventory
receipt must be version 2, use the exact synthetic-data policy,
identify its independently approved state by SHA-256, report profile `pre`,
have no errors, and carry a valid canonical receipt digest. Raw rows, object
names, identifiers, credentials, connection strings, Redis keys, or provider
payloads are forbidden. The resource identity is the public control-plane
identity pinned by policy, not a provider response or secret.

The quiescence receipt uses the string state `quiesced`; no input boolean can
authorize it. The server-scope receipt is deliberately a placeholder with
state `unimplemented` and blocker `server_scope_collection_unimplemented`.
Both receipts are structural claims until their attestations are verified.

Evidence expires after at most 15 minutes. The validator rejects evidence
observed in the future, expired at validation time, or issued with a longer
lifetime. Its clock is the validator's UTC system clock, not a manifest input.

## Local validation

```text
python scripts/validate_staging_evidence_manifest.py \
  --bundle /absolute/protected/path/to/evidence-bundle
```

Exit 0 and `outcome: contract_valid` mean only that the local bundle is
well-formed, fresh, internally consistent, and byte-bound. The output always
states `activation_state: blocked` with these unresolved milestones:

- server-wide PostgreSQL scope collection
- cryptographic verification of signer attestations and protected-ref claims
- trusted provider access and source-resource observation
- workflow integration and protected evidence storage
- restore/sanitizer activation

No valid v1 bundle authorizes a restore, sanitizer, rehearsal, staging cutover,
or production action. A later, independently reviewed milestone must replace
the server-scope placeholder, verify attestations against trusted issuer data,
collect provider evidence, and explicitly change the activation contract.
