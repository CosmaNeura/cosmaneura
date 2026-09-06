# Controller receipt v2 sanitizer binding

## Risk and outcome

Tier 2: this leaf replaces the sanitizer's unauthenticated legacy-v1 receipt
shape with a dormant in-process authority boundary. It starts from exact parent
`1135f75d6583783b416617b5118501876b3d91a1`, accepts only a sealed
`WrittenControllerReceiptV2`, and produces a sealed `SanitizerTargetBinding`.
It does not connect to PostgreSQL, mutate data, call a provider, or enter a
workflow.

## Trust boundary

- `SanitizerTargetBinder.bind` has one keyword-only public data input:
  `written_receipt`. Caller-supplied bytes, mappings, paths, URLs, DSNs,
  hostnames, roles, digests, policies, status flags, and timestamps cannot
  select the authority.
- Direct construction, subclasses, lookalikes, content-only validator results,
  private-reader results, unwritten builder results, and both raw v1 and v2
  records fail closed. There is no compatibility fallback.
- The reviewed private factory supplies a whole-second UTC clock. The binder
  rejects invalid or regressing time, expired receipts, stale retained
  observations, and WORM retention that has expired.
- Binding reauthenticates the writer result and the retained builder,
  production, ARM, database, and isolation-inventory authorities. It also
  rebuilds the observation projection at binding time and revalidates the
  canonical intent, submission, and receipt bytes with the shared validator.
- The sealed output binds the rehearsal, exact target FQDN and PostgreSQL
  server resource, `staging` database, authenticated database role, approved
  restore point, source/candidate/workflow SHAs, run ID/attempt, receipt digest,
  and expiry. Its private writer reference and proof make field or ancestor
  substitution detectable; the object has no serialization API.
- The output carries only the narrow facts already authenticated by its
  ancestors. Provider history, writer identity/route use, whole-execution copy
  absence, mutation authority, sanitizer authority, runtime authority,
  workflow integration, live-provider calls, and executable status remain
  false.

## Activation boundaries

The policy continues to set `trusted_evidence_binding_implemented=false`,
`database_mutation_implemented=false`, `executable=false`,
`workflow_integrated=false`, and `live_provider_calls_enabled=false`.
`validate_target_url` accepts only the sealed binding and derives its expected
host, database, and role from that binding. `preflight` authenticates the seal
and then stops before the supplied connector can run. The public binder factory
also stops unconditionally.

This slice deliberately retains no source rows, fixed-catalog row values or
source-state HMACs, transform key, credential, token, DSN, or caller-selected
storage locator. Authenticated server-scope evidence, fixed-catalog source-state
binding, the executor, post-projection proof, transaction/rollback proof, and
workflow integration remain later bounded work.

## Verification and release

Run the focused sanitizer, writer, builder, content-validator, and private-reader
tests; Ruff check and format verification; Pyright; compileall; raw policy-hash
checks; and the repository change-budget check. Obtain independent review of
the exact final commit and record its commit, tree, findings, and dispositions
on the PR before merge.

An ordinary merge must not deploy. Infrastructure binding remains blocked on
the serial-7 base-recovery evidence. A founder must separately approve and run
any canonical staging rehearsal or cutover from an exact protected-master merge
commit. Production promotion remains a separate intentional founder-only act.

## Recovery

Before activation, revert this leaf. It creates no resource and changes no
data. After a future activation, disable the workflow/consumer binding first,
preserve immutable evidence, and use cleanup-only handling for any ambiguous
outcome; do not re-enable the legacy raw-v1 receipt path.
