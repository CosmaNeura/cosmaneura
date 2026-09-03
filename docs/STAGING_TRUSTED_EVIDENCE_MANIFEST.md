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
fail. The validator rejects unknown fields and every caller-supplied JSON
boolean except the collector's exact top-level server-scope `pass: true` field.
The manifest and its candidate, inventory, and quiescence receipts bind one
source SHA, candidate SHA, source Azure resource identity, observation/expiry
window, protected repository/ref, workflow file and SHA, run ID/attempt,
dispatch event, signer claims, and versioned policy digests. The inventory HMAC
key identifier must be the supported `staging-inventory-hmac-v1`; key material
never enters the bundle.

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
authorize it. `server-scope.json` now has the exact output shape emitted by
`staging_server_scope_evidence.collect_evidence`. Its source revision must equal
the manifest source SHA, its policy digest must equal the pinned server-scope
policy digest, and its capture must complete no later than the manifest
observation within the same 15-minute bound. Server and database surface sets,
count/HMAC shapes, the single primary identity, canonical database ordering,
empty-secondary invariants, and bounded record totals all fail closed. The
manifest independently binds both the exact file bytes and the SHA-256 of the
receipt's canonical JSON; the collector receipt has no synthetic embedded
`receipt_sha256` field.

The committed server-scope policy remains `activation_ready: false` with exact
configuration placeholders. In this dormant state the manifest validator
accepts a structurally valid real collector receipt but does not treat
placeholder policy pins as an authorization baseline. Once a separately
reviewed policy is activated, receipt revision, HMAC key version, every server
surface, and every database status and surface must match its exact pins.
Locally, `source_resource_hmac` and `evidence_hmac` are checked only for their
closed-world digest shape because this validator intentionally receives no
server-scope HMAC key. Authenticated source-resource and signer-attestation
verification therefore remain explicit blockers.

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

- exact trusted server-scope policy configuration
- cryptographic verification of signer attestations and protected-ref claims
- trusted provider access and source-resource observation
- workflow integration and protected evidence storage
- restore/sanitizer activation

No valid v1 bundle authorizes a restore, sanitizer, rehearsal, staging cutover,
or production action. A later, independently reviewed milestone must configure
and re-pin the server-scope policy, verify the receipt HMAC and signer
attestations from trusted key/issuer data, bind authenticated provider evidence,
and explicitly change the activation contract.
