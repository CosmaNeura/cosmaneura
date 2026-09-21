# Flight plan: dormant launch-authority seals and byte ingress

## Outcome and boundaries

- Tier **2** preserves exact validated evidence/attestation Blob selectors,
  HMAC and GitHub-root Key Vault selectors, and cleanup mode as opaque authority
  bound to the canonical launch-record bytes.
- The exact authenticated GitHub-root bytes remain inside a sealed in-process
  carrier that the provision handoff encloses without copying authority fields.
- The evidence validator gains a path-free entry point for one exact built-in
  byte mapping. It snapshots the map, then shares the complete existing checks.
- No new network reader or I/O is implemented; the existing dormant reader only
  returns bytes it already authenticated. Dependency assembly, cleanup,
  tombstone consumption,
  workflow/Terraform changes, Azure calls, rehearsal, restore, cutover, and
  activation are explicitly out of scope.

## Risk and dependencies

- Caller substitution, mutable aliases, selector reduction, or a second root
  read could change authority after validation.
- Authority children carry a canonical digest and seal. Their parent reparses
  the canonical record and checks each selector before use. Legacy digest
  accessors derive from those authorities, preventing two sources of truth.
- The init-disabled runtime carrier binds exact record/root bytes to a sealed
  signature-proof snapshot; policy bounds and the root pin are rechecked.
- Ordering remains receipt/runtime authentication -> this authority-preserving
  slice -> separately reviewed private evidence/HMAC readers and dependency
  assembly -> cleanup executor/tombstone consumer -> workflow integration ->
  authorized non-PHI rehearsal.

## Current state and recovery

- Base `7788a7d1a7e6243542d477cdecd506cf08a5f862` discards selector detail and
  trusted-root bytes after validation.
- This slice is code and contract only. Recovery is a code-only revert; there
  is no schema, artifact, credential, infrastructure, or data recovery action.

## Release controls

- The policy records future sources as `dormant_unintegrated_contract`; both
  dependency assembly and workflow integration remain false.
- Existing public path inputs and executor behavior are unchanged. The public
  dependency factory still refuses unconditionally, and no activation flag is
  changed.

## Validation and rollout

- Unit evidence covers selector order, cleanup modes, tamper and construction
  refusal, root retention, proof binding, byte/path equivalence, no bundle-path
  read by the byte API, map detachment, and bounded malformed-map rejection.
- Completion requires focused and broad offline tests, Ruff, Pyright, policy
  digest validation, diff-budget checks, and independent review of the final
  local commit.
- Hold after the local commit. A later PR must assemble concrete dependencies
  and revalidate the parent launch record before using any selector. That PR
  must not infer permission to activate, call Azure, or execute cleanup.
