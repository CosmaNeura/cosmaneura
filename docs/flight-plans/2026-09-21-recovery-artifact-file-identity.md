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

There is no schema change, data migration, feature flag, or live recovery action.
