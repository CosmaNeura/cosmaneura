# Staging trusted-evidence manifest contract

Status: **CONTRACT VALIDATION ONLY — ACTIVATION BLOCKED**

`contracts/staging-rehearsal/v1/evidence-manifest-policy.json` defines the
closed-world v2 evidence bundle. `scripts/validate_staging_evidence_manifest.py`
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

The v2 manifest also names one `approved_restore_time`. For this first
fail-closed contract it must be canonical UTC and exactly equal
`observed_at`; the quiescence receipt is bound to that same observation. A
lagged point-in-time restore is not accepted until quiescence evidence can
authenticate an interval that contains it. This removes the independent
operator-supplied restore-time choice without overstating what the current
evidence proves.

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

The result also contains an exact `evidence_binding` projection for the dormant
safe-submit receiver. It binds the manifest and checksum-file byte digests,
evidence-set and artifact receipt digests, source/candidate identities,
approved restore point, candidate approval metadata, protected workflow/run
provenance, and claimed signer identity. This projection is deliberately not a
trusted authorization: the local validator cannot create the attestation,
provider-observation, or server-scope-authentication receipts required by the
safe-submit v4 receiver.

## Dormant server-scope authentication receipt

`contracts/staging-rehearsal/v1/server-scope-authentication-policy.json` and
`scripts/authenticate_staging_server_scope_receipt.py` define the first trusted
producer slice. Its supported entry point invokes the closed-world validator
itself and loads the exact hash-pinned checked-in authentication, manifest, and
server-scope policies; callers cannot inject replacement policies or supply a
purportedly validated projection. The validator
mints an opaque immutable snapshot from the public-result and
`server-scope.json` bytes read in that one pass, and only a private helper
consumes it. The authenticator never reopens an evidence file after validation
and reparses each snapshot byte string exactly once into owned objects. It then
uses the collector's shared
HMAC implementation to verify the source-resource identity, primary-database
identity, complete signed receipt, configured policy pins, revision, and key
version. Authentication and server-scope policy mappings are likewise parsed
from the exact raw bytes named by their SHA-256 digests, with the latter digest
pinned inside the former policy; byte subclasses are not accepted. Their source
resource and primary-database claims must agree. The trusted clock is sampled
only after HMAC verification; that one value controls both freshness and
`authenticated_at`, while the authenticated capture interval must remain inside
the manifest observation window.
Its canonical aggregate-only receipt carries independently derived
digests for the exact file and canonical receipt and emits the digest required
by safe-submit's `server_scope_authentication_receipt_sha256` field.
The CLI writes the complete result only to the required absolute
`--output-file`, created exclusively with mode `0600`; it does not print the
receipt or stable keyed identifiers to job logs.

This component has no network, subprocess, provider, restore, sanitizer, or
deployment capability. Both its own checked-in policy and the server-scope
policy are disabled, and the command refuses before reading a key. A future
activation must deliver the exact owner-controlled key through the private
executor, configure and re-pin the server-scope baseline, and preserve the key
file byte semantics (including any trailing newline). A successful HMAC receipt
still leaves provider observation, GitHub attestation verification, workflow
integration, and restore activation blocked.

No valid v2 bundle authorizes a restore, sanitizer, rehearsal, staging cutover,
or production action. A later, independently reviewed milestone must configure
and re-pin the server-scope policy, verify the receipt HMAC and signer
attestations from trusted key/issuer data, bind authenticated provider evidence,
and explicitly change the activation contract.
