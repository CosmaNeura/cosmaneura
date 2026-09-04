# Flight plan: dormant launch-receipt executor handoff

## Outcome and boundaries

- Tier **2** connects the reviewed provision mailbox receiver to the trusted
  executor's private launch-material seam in one process. The handoff accepts
  no caller action, URI, path, digest, or runtime pointer.
- In scope: sealed-receipt consumption, receipt-derived runtime references,
  bounded exact launch/runtime bytes, runtime-signature proof cross-binding,
  executor carrier ownership, focused tests, and runbook updates.
- Exact private launch/runtime Blob readers and external-runtime Sigstore/root
  authentication remain injected future adapters. Broker sending, Azure,
  Terraform, workflows, restore, and live calls remain out of scope.

## Ordering and invariants

- Preserve receiver-first order: validate an executable-shaped runtime policy,
  receive and authenticate one `provision` receipt, derive both job-scoped
  runtime references from those same bytes, read the exact launch record once,
  authenticate the exact runtime record once, then return one same-process
  `ProvisionLaunchMaterial` to the executor.
- The launch and runtime byte digests must equal the receipt-derived pins. The
  runtime proof must be the exact sealed verifier result and bind the runtime
  record, Sigstore bundle, independent root, issuer, and certificate identity.
- Before either record source runs, both runtime URIs must exactly match the
  policy's HTTPS private-ingress host, container, digest-addressed path, and
  suffix, with no user info, port, query, fragment, or encoded alternate path.
- Recheck `CONTAINER_APP_JOB_EXECUTION_NAME` between authority stages. Use the
  executor's one non-regressing clock for receipt observation and later launch
  validation. Do not serialize or reconstruct opaque proof objects.
- The handoff adds no retry. Only the receiver's existing 404-only polling may
  retry. A missing, malformed, oversized, substituted, or ambiguous result
  refuses before later evidence reads or any submit path.

## Activation and recovery

- Both checked-in policies remain disabled with every activation and
  implementation assertion false. `_runtime_dependencies()` remains an
  unconditional refusal, so the public executor cannot reach this handoff.
- No infrastructure, credentials, network grants, workflow, image, schema,
  data, or environment changes are made. Recovery is a code-only revert while
  the dormant policies remain off.
- Later reviews must separately implement external-runtime Sigstore
  authentication, exact private Blob and versioned Key Vault ingress, the
  default dependency assembly, broker publication, and live binding evidence.

## Verification

- Exercise the real mailbox receiver through the handoff with sealed fake
  transport responses. Cover exact call order/count, provision-only behavior,
  byte bounds and digests, proof and policy cross-binding, environment
  stability, shared clock identity, unsealed/substituted objects, bounded
  exception text, and refusal before sources under the checked-in policy.
- Run the focused handoff, mailbox, launch-binding, runtime-binding, executor,
  and adapter tests; the broader staging-restore suite; Ruff format/lint;
  Pyright; policy validators; repository diff checks; and the 2,000-line,
  50-file change-budget check. Record independent final-head review before
  merge.
