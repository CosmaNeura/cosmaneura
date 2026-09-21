# Staging sanitizer executor trusted-evidence preflight

Tier 2: this dark receiver proves that the sanitizer executor can consume and
reauthenticate the exact trusted-evidence authority at its current clock. It
does not connect to a database, invoke a provider, transform a row, mutate
state, mint an execution result, or enter a workflow.

## Outcome and exact dependency

This slice starts from exact trusted-evidence commit
`90c0324f1895bf8f45f17d0bd0eb61e30a0741ac`. The required order remains:
controller receipt builder, private-WORM writer, sanitizer target binder,
fixed-catalog source-state binder, trusted-evidence join, then this preflight.

`SanitizerExecutorPreflight.preflight` has one keyword-only public data input:
an exact sealed `AuthenticatedSanitizerEvidence`. The type, its seal, and its
current-time verifier live in an adapter-free authority leaf. Importing the
executor therefore does not import the upstream safe-submit, runtime, provider,
attestation, process, or database-client modules. The safe-submit module
re-exports the leaf's exact `RestoreEvidenceBinding` type so the existing
composition boundary retains one type identity without pulling its adapters
into this receiver.

The preflight accepts no caller bytes, mappings, paths, locators, database
settings, connectors, roles, policies, keys, timestamps, or runtime overrides.
A private reviewed configuration seam supplies its clock.

The clock must produce whole-second UTC values and cannot regress between the
initial and final reads. The evidence's original `authenticated_at` must not be
in the future, and the retained controller target binding must remain fresh at
the final read.

## Current-time ancestry authentication

The preflight does not treat the evidence object's old proof as sufficient. It
uses the adapter-free authority leaf's single internal current-time
reauthentication path to replay the complete retained ancestry:

- the exact evidence type, in-process seal, private ancestors, policy bytes,
  canonical evidence bytes, public fields, false authority claims, and proof;
- the retained fixed-catalog source object's mint-time exact type and seal,
  checked before invoking only that class's own reauthentication method;
- the exact final restore-evidence binding and complete canonical equality with
  the controller intent's retained evidence record;
- every source, candidate, resource, database, restore-point, workflow,
  run/attempt, signer, manifest, inventory, quiescence, server-scope,
  server-authentication, provider, and attestation binding;
- the fixed-catalog source-state authority and exact ordered projection; and
- the retained writer, builder, isolation inventory, database observation, ARM
  observation, production authority, and canonical controller records at the
  executor's current time.

The restore-evidence receipt's expiry remains historical safe-submit evidence;
it is not an executor deadline. The current deadline is the controller target
binding expiry, checked as `authenticated_at <= now <
target_binding_expires_at` at the final non-regressing clock read.

## Deliberate terminal refusal

Successful authentication always terminates with
`activation.data_contract_executor_unimplemented`. The preflight produces no
output object and exposes no connection, mutation, provider, post-projection,
runtime, sanitizer, executable, or workflow authority. Its canonical policy
subcontract is closed world and records all of those authority flags as false.

The following activation holds remain unchanged:

- the data-contract executor and all semantic row mappings remain unimplemented;
- fixed-catalog post-projection preservation is unauthenticated;
- guarded-table permission and rollback proofs do not exist;
- atomic serializable execution, session exclusion, timeouts, ambiguous-outcome
  cleanup, and fresh-connection post-commit verification are unimplemented;
- no database or provider adapter is imported or invoked;
- upstream evidence construction remains disabled or unconfigured; and
- no protected workflow, staging rehearsal, cutover, or production promotion is
  authorized by this slice.

## Verification and recovery

Tests exercise the real retained controller/source/evidence chain after the
historical restore-evidence expiry but before target expiry. They prove both
clock reads drive current-time ancestry reconstruction; refuse mappings,
subclasses, forged seals, field and retained-ancestor tampering, future
authentication, expiry, non-UTC/subsecond/regressing clocks, policy drift, and
unexpected internal errors. A fresh isolated interpreter also proves that an
executor import loads none of the safe-submit, runtime, provider, attestation,
process, Azure, or database-client modules; static checks prohibit database,
network, provider, process, transaction, and output surfaces.

Run the focused executor, evidence, fixed-catalog, sanitizer, composition, and
safe-submit suites; Ruff format/lint; Pyright; compileall; canonical raw-hash
checks; and staged diff/change-budget checks. Obtain independent review of the
exact final commit before publication.

Fresh CI on the first published candidate completed every other worker and 45
of 50 combined sanitizer-runtime cases without an assertion failure, but that
worker was cancelled at its unchanged 20-minute fail-closed cap. The evidence
and executor files then ran as two exact, disjoint workers in all three workflow
matrices.

The first split-worker run then passed all 20 behavioral executor cases but
failed its final fresh-import probe because pytest-cov's site startup had
preloaded `subprocess` before the executor module was evaluated. The probe now
adds Python `-S` to its existing isolated-interpreter invocation, excluding
site initialization and third-party instrumentation so it measures only the
executor's import closure. A local pytest-cov reproduction passes this form.

The corrected-import run passed the executor worker, while the evidence worker
passed 26 of 29 cases through 89% before the same 20-minute cap cancelled it.
The evidence file now runs as two explicit whole-function node-selector workers:
19 policy/digest/API cases and ten chain/integrity cases. Collection
tests prove both groups nonempty, unique, disjoint, and exhaustive against all
29 evidence nodes. The restore worker excludes the complete sanitizer-runtime
family as before, and an unknown future sanitizer-runtime suffix remains
unassigned rather than silently routed.

There is no deployment or data recovery action because the code is dormant and
unreachable from a workflow. Recovery is a code-only revert of this leaf. A
future executor implementation must be a separate bounded Tier 2 review and
must preserve this preflight as a receiver-side gate before any I/O authority
is assembled.
