# Flight plan: authenticate dormant broker launch receipts offline

## Outcome and boundaries

- Problem and measurable outcome: replace loose broker-signature claims with a sealed proof produced only after offline ES256 verification of the exact canonical launch-receipt bytes.
- In scope: a receiver-side verifier, a closed-world `.signature.json` envelope, an exact P-256 public JWK and Azure Key Vault signing semantics in the runtime-binding policy, the receipt adapter proof boundary, focused tests, and downstream digest pins.
- Explicit non-goals: executor wiring; HTTP, managed-identity, or Blob polling; broker/OIDC/start senders; external runtime-record Sigstore verification; Azure, Terraform, workflow, restore, or live action.
- Affected systems and data: dormant staging restore contracts only. No environment or data is accessed or changed.

## Risk and dependencies

- Tier and reason: **Tier 2** because this authenticates authority-bearing input immediately before the future trusted executor.
- High-blast-radius decisions: ES256 means SHA-256 of the exact receipt bytes, Azure Key Vault receives the base64url-no-padding 32-byte digest, and the wire signature is exactly 64-byte IEEE-P1363 `r || s`. The receiver requires low-S and converts to DER only inside `cryptography`; a future sender must normalize Key Vault's returned `s` to `min(s, n-s)`.
- Dependency order: runtime policy and receiver proof first; mailbox transport and executor wiring later; broker signing sender only after the receiver is merged. Each layer can remain dormant indefinitely.
- Contract order: the launch receipt uses `.signature.json`. External runtime-record signatures remain Sigstore bundles with `.sigstore.json` and are unchanged.

## Current deployed state

- Protected master has a dormant launch receipt contract but no concrete receipt-signature verifier, mailbox transport, broker sender, or executable adapter.
- This slice adds no deployed behavior. Every runtime-binding and trusted-executor activation/implementation flag remains false.
- Recovery boundary: revert this dormant contract before any later sender or adapter is activated.

## Feature flag / release control

- N/A for user-visible behavior. Existing `activation_ready`, `launch_receipt_binding_implemented`, receipt-signing implementation, mailbox adapter, and trusted-executor adapter controls remain false.

## Schema/data sequence

- N/A: no schema or data changes.

## Rollout and promotion

- Hold points: canonical policy/hash validation → known-answer and adversarial cryptographic tests → broader dormant restore tests → independent review → merge dormant → separately review receiver transport → separately review sender and activation.
- Abort on any noncanonical/oversized input acceptance, duplicate or unknown envelope field, metadata mismatch, invalid/off-curve JWK, non-P1363 or high-S signature, proof reuse, unsealed construction, or activation-bit change.
- No live canary or production/PHI promotion is authorized by this slice.

## Security and recovery

- The proof binds exact receipt and envelope bytes and digests, policy ID/binding, algorithm, key resource/version, and canonical public-key digest. Errors are one bounded code and contain no input material.
- The policy pins canonical JSON without a trailing newline, strict base64url without padding, exact 32-byte P-256 coordinates, and an exact 4096-byte envelope limit.
- Recovery is code-only while dormant: revert the receiver/policy changes. Future sender rollout must preserve receiver-before-sender ordering and low-S normalization.

## Completion

- Required evidence: fixed cryptographic vector, tamper/wrong-binding/malformed/high-S/unsealed/reuse tests, policy and downstream hash validation, Ruff/format, Pyright, broader staging-restore tests, and change-budget checks.
- Follow-ups: implement the exact-object private mailbox receiver transport, wire its output into the executor, then separately implement broker signing/publication and activation evidence.
