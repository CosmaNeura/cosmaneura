# Staging controller receipt v2 builder flight plan

## Classification and outcome

Tier 2: this is a dormant security-boundary component in the restore control
plane. The slice adds a pure builder that can produce exact controller receipt
v2 bytes only from the sealed isolation-inventory authority. It performs no
provider, database, storage, workflow, or mutation operation.

The builder reauthenticates the complete in-process chain at one trusted
whole-second UTC time:

1. production authority and exact intent/submission source;
2. launch record, broker receipt, signed runtime record, and policy bindings;
3. exact ARM target/network observation;
4. read-only database role, TLS, and private-route observation; and
5. point-in-time isolation inventory regenerated from retained ARM responses.

It maps only authenticated upstream values into the closed-world receipt,
computes the controller policy-binding and runtime resource-set digests, clamps
expiry to every upstream deadline, and passes the exact bytes through the
shared controller receipt v2 content validator.

## Deliberate boundaries

- The only public data input is the sealed isolation-inventory authority. The
  trusted clock is supplied only by reviewed configuration.
- There is no caller field, digest, time, path, URI, policy, selector, or pass
  override.
- There is no writer, reader, consumer, sanitizer, executable, or workflow
  integration in this slice.
- `controller_receipt_exists`, create-only behavior, writer identity use,
  full three-record source authentication, broad provider facts, execution
  history, copy absence, mutation authority, and runtime authority remain
  false.
- The in-process seal preserves the upstream provider provenance for an
  immediate reviewed writer or consumer. Persisted receipt bytes alone do not
  preserve that provenance and must never be upgraded into those claims.

Receipt schema version 2 remains unchanged because no producer, writer, live
integration, or external v2 receipt existed before this pre-activation slice.

## Ordered rollout

1. Merge the independently reviewed production, ARM, database, isolation
   semantics, and isolation-inventory prerequisites.
2. Rebase this branch onto their exact merged head and rotate the builder leaf
   pins if any prerequisite policy bytes changed.
3. Run focused builder and adjacent receipt-chain tests, static checks, policy
   hash verification, and the repository change-budget check.
4. Obtain independent review of the exact final commit and record the finding
   durably on the PR.
5. Merge only with protected CI green and an exact head/tree match.
6. Add the conditional private-WORM writer and downstream consumer bindings in
   separate reviewed slices. Keep every activation flag false until those
   slices and an approved live rehearsal are complete.

## Recovery

Before activation, recovery is a normal revert of this leaf-only slice. No
cloud state, stored receipt, schema, data, or workflow is changed. After a
future writer consumes this seal, rollback must first disable the future
workflow/consumer binding; immutable records are evidence and are never
deleted as rollback.

## Verification

- focused builder tests cover policy pins and dormancy, the sole-input API,
  exact field and digest derivation, shared-validator acceptance, ancestor and
  mixed-chain tampering, causal/freshness/deadline checks, strict integer/pass
  behavior, output seal tampering, sanitized failures, and absence of live I/O;
- adjacent content, production, ARM, database, isolation-inventory, and private
  reader tests remain green; and
- Ruff, formatting, Pyright, compileall, raw policy hash, and diff-budget checks
  run against the exact final tree.
