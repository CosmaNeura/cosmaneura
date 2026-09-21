# Recovery artifact file identity correction

Risk: Tier 2, because the staging recovery validator enforces a file-integrity
boundary. The owner authorized this correction while resolving PR #448's local
Backend failures. This plan authorizes code verification and merge only.

## Failure and correction

`_read_protected_file` requires one hard link before reading, then compares the
opened file's identity and timestamps after reading. A replacement can unlink
that inode without changing its recorded timestamps within the filesystem's
timestamp granularity. The reader then misses the replacement, although it still
returns the originally opened bytes.

Compare the real link count before and after the read as part of the existing
identity tuple. Keep the existing ownership, mode, size, symlink, and timestamp
checks. A parent-directory move remains permitted because the open file retains
its identity and link count.

Update the recovery launcher's validator pin, both consumers' launcher pins,
the state-refresh validator's recovery pin, and its consumer's bootstrap pin
together. These pins must be computed from the committed bytes; no pin check is
removed or changed into a dynamically accepted value.

The unrelated stale deployment-test digest is refreshed to the exact unchanged
`infra-staging/deploy.sh` from protected-master base `e362108f`. Its previous hash
predates the already-merged deployment corrections through `e0691079`.

The full suite also exposed the same timestamp-granularity assumption in
`NativeFiles.read`: same-size content writes can evade metadata comparison.
Reread at most the captured length plus one EOF byte through the same held
descriptor, compare the bytes, then retain the existing final ACL, file, and
parent checks. This remains a bounded consistency check for trusted code, not
an atomic snapshot or a sandbox against malicious code under the same user.
Update its bootstrap source pin and the command formatter's loader and source
bundle pins together. A deterministic regression holds timestamp observations
constant while changing real file contents.

Snapshot unit tests use private module names so their imports cannot replace
classes retained by other fixtures. Existing 45-minute handoff and 30-minute
provenance contracts are reflected in fixture timestamps. Linux shell fixtures
translate only the BSD stat formatting to its GNU equivalent, retaining real
size, mode, owner, link, hash, terminal, and environment checks.

## Verification and merge

1. Force an unchanged `ctime_ns` during a real file replacement. Confirm the
   regression fails before the correction and passes afterward.
2. Verify the normal replacement, allowed parent move, ownership/mode checks,
   launcher authentication, and state-refresh pin chain.
3. Run the local Backend, Frontend, and change-budget checks with synthetic
   services and no live credentials. Review the final substantive commit
   independently and record the review and results on PR #448.
4. Merge the verified PR head only after those checks pass. Deployment remains
   a separate founder-controlled action.

## Recovery

If the corrected reader rejects a legitimate recovery input, stop that recovery
operation and retain its original evidence. Do not loosen file guards or pins.
Revert the correction commit as a unit, including its five dependent pin updates,
through a reviewed PR if rollback is required; this restores the prior reader
and its matching authentication chain. Reverting the full PR merge restores the
prior deployment workflow, tests, and documentation together.
The native-reader correction likewise must be reverted together with its
bootstrap, loader, and source-bundle pins.

There is no schema change, data migration, feature flag, or live recovery action.
