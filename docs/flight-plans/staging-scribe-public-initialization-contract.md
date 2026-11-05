# Dormant Scribe public initialization contract

Tier 2: runtime security, cancellation and deployment configuration boundaries.
This source slice is pure validation and plan preparation. It adds no provider
executor, deployment switch, workflow input, publication path or live permission.
The default is no plan (`None`), which returns disabled without inspecting state.
John owns any later exact policy/cohort/release decision. Actions remains off.

## Contract and inputs

The complete desired native/activation/cancellation group contains exactly 13
public fields. API receives all 13; worker and scheduler receive only ledger
readiness. Existing public/native validators validate the exact groups and
staging authority binding without loading Core application configuration.

Plans bind candidate, controller, selected PR, founder, staging identity digest,
schema before/after, immutable image, created/expiry times (maximum 48 hours),
and independently pinned complete before/desired fingerprints. Each workload
also binds its exact revision/image and full state, template, configuration,
identity and private-value hashes. Raw state includes ordered environment
entries, explicit presence/absence, complete app secret objects, private values,
command/args, resources/scale and configuration/identity fields. JSON object key
order and formatting are canonicalized; list order and string values are retained.

The pure transformation copies baseline state. It changes only source-bound
image, bound revision metadata and allowlisted public controls. It preserves
private values, references, secret objects and every unowned field. Controls
with empty values remain explicit public empty entries; there are no removals.
An absent unrelated health-alert field remains absent. Absence and `false` are
distinct hashes. Any incompatible resolver projection must be refused by the
dependent wiring, never stripped or normalized into equality by this contract.

No secret state is put in a plan or CLI output. Protected state may be consumed
in memory/stdin by future approved wiring; this slice's tests use invented values
only. CLI stdin is `{plan, observed, context, completed}`. Outputs contain hashes,
phase names and allowlisted public updates. Errors are fixed, content-free codes.

## Sequence and recovery

Initialization order is worker, scheduler, API. Initial transition validation
requires the exact baseline schema and raw baseline states, with API acquisition
and cancellation intake closed. Phase validation requires actual schema-after
readiness, exact untouched baseline states, and source/plan-bound completed rows
for already updated workloads. A matching target-looking revision without its
completed row is refused. Both processing workloads must reach ledger-ready
desired state before API acquisition controls become the next permitted phase.

The dependent executor must independently authenticate the supplied context,
pin the reviewed plan hash, retain all canonical Git/PR/controller/image/schema/
identity gates, write durable intent/results before/after each operation, and
reobserve immediately before mutation. This validator does not prove a supplied
context came from GitHub, Azure or an approved owner. It performs no mutation.

Closed recovery uses an explicitly compatibility-bound image from the same
candidate source. It preserves native workload/context authority for authorized
status/read/export, policy, private state, identity, ledger readiness on every
processing workload, receipts and expanded schema. It closes activation/cohort,
visit acquisition/actions and cancellation intake. Raw old-template rollback is
refused. Expired authority requires a fresh reviewed recovery binding; this
contract does not silently extend owner authority or implement automatic expiry.
Previously queued remote speech/translation cancellation remains a separate gap.

## Validation and dependent wiring

Focused tests run with plugin autoload disabled and Core root conftest excluded.
They cover allowed scope, private/ref/secret-object drift, environment duplicates
and order, exact absence, other template/identity drift, source/PR/schema/expiry
binding refusal, complete groups, ordered phases, missing results, API precedence
and closed recovery. They load only pure scripts and never Core app, SDKs or cloud.

Before any executor is connected, independently review this exact commit and the
dependent canonical/controller/closed-recovery wiring. Add the helper to the
trusted dependency inventory without weakening existing verification. Fresh
desired-runtime build/publication evidence must retain complete immutable plan
binding; the retained dark artifact cannot be relabeled. This contract grants no
local release exception, Actions re-enablement, merge, deployment, migration or
cohort activation. Dormancy is recovery for unused source only; it cannot undo an
accepted cancellation or substitute for a qualified live recovery artifact.
