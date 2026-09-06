# Controller receipt v2 private-WORM writer

## Risk and outcome

Tier 2: this leaf adds a dormant storage write security boundary. It accepts
only `BuiltControllerReceiptV2`, derives the provision identity and exact Blob
URL from its retained sealed policies, conditionally writes the exact canonical
receipt once, and reads that object back once. It is not workflow-integrated or
live-enabled.

## Preconditions and invariants

- Reauthenticate the builder and its production, ARM, database, and isolation
  ancestors before credentials and around every provider checkpoint.
- Obtain one storage token, issue one `If-None-Match: *` Block Blob PUT, then
  issue one exact GET. Never pre-read, retry, poll, list, overwrite, delete,
  redirect, use an ambient proxy, or materialize the record on disk.
- The public factory is unconditionally disabled. Tests can use only the
  private reviewed-configuration factory, which requires explicit hardened
  identity and Blob openers plus its clocks and deadline boundary; it has no
  live-capable opener defaults.
- Bound identity, PUT, GET, and total elapsed time independently.
- Authenticate exact bytes, length, MD5, strong ETag, last-modified time,
  service version, server encryption, legal hold, and a Locked immutability
  interval of at least 90 days on GET.
- Require `Last-Modified` to be no earlier than the built receipt creation time
  minus the allowed clock skew, and require retention to remain in the future
  at validation time as well as spanning at least 90 days from modification.
- Treat only the reviewed exact 201 response shape as `created`. Treat every
  other status or post-dispatch exception as indeterminate and allow success
  only after exact readback as `reconciled_indeterminate`. The provider evidence
  needed for a `preexisting_exact` classification is deliberately absent.
- A result may assert receipt existence and an authenticated three-record
  source because it retains the sealed prefix authority and exact GET proof.
  It must not assert create-only behavior, writer identity or route use,
  provider facts, execution history, copy absence, sanitizer/database mutation,
  runtime authority, workflow integration, or executable activation. Persisted
  receipt bytes alone do not preserve upstream provenance.

## Release sequence

1. Review the five-file leaf and its exact policy digest.
2. Run focused writer, builder, content, and private-reader tests plus Ruff,
   formatting, Pyright, compileall, policy-hash, and change-budget checks.
3. Obtain independent review of the final commit and retain the exact commit,
   tree, findings, and dispositions on the PR.
4. Merge only after required checks pass. Ordinary pushes must not deploy.
5. In later PRs, add the dedicated container, Locked retention/legal hold,
   create-read role, exact identity assignment, accepted provider evidence,
   hash/status cascade, and workflow binding. Those changes remain blocked on
   completion of the existing serial-7 infrastructure recovery.
6. A founder performs any canonical staging rehearsal/cutover manually from an
   exact protected-master merge commit. Production promotion remains a separate
   intentional founder-only decision.

## Recovery

Before activation, recover by reverting this leaf; it owns no live resources or
data. After later activation, disable the workflow binding first. Never delete
or weaken the immutable container as rollback; retain evidence and reconcile
any indeterminate PUT through the one permitted exact readback path.
