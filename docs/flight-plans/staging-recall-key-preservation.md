# Preserve the installed staging Recall key

Tier 2: staging credential admission and deployment configuration. A fresh
authenticated read of the existing vault field differs from the common key on
the three current Core workloads. Preserve the installed configuration through
one explicit `RECALL_API_KEY_INPUT`; leave the default vault path unchanged.

The staging-only guard requires the fixed tenant/subscription/origins, exactly
the three owned healthy ready containers and their existing `recall-api-key`
references, and a common key hash equal to the input. Two read-only snapshots
must match, including key hashes, revision pointers and public Recall origin.
The input is not exported to the app or forwarded from ordinary ambient
`RECALL_API_KEY`. Azure subprocesses receive no resolved application secrets.
The existing full identity and effective-configuration checks remain mandatory.

This source change writes no vault/credential/configuration, changes no flags,
Scribe apps/jobs, schema or production resource, and grants no deployment
permission. Review and merge it normally before binding the final release.
Validate rejection of mismatched keys/targets/refs, malformed or changing live
state, provider failures, secret output or subprocess leakage, and preservation
of the default path. No installed credential or database rollback is part of
this source amendment.

The hosted deployment step now acquires only the existing Core API's
`recall-api-key` through the fixed subscription and resource group. It keeps
the bounded value in the step's shell, passes the explicit input to the
unchanged resolver/three-workload guard, and clears it on success or failure.
An empty or failed read refuses before deployment instead of falling back to
the vault. The deployer already clears the input immediately after resolving
configuration. No key is written to `GITHUB_ENV`, an argument, a file or output;
Azure logging/telemetry and dynamic installation are disabled for this read.

M2's sanitized failed release and the subsequent ten-field read show only the
Recall API fingerprint differed; the other nine fields and complete ready
configuration matched. This repair preserves that installed value and all
identity/configuration/source/image/schema gates. It adds no artifact-recovery
route. A new reviewed merged candidate must build normally. Reverting the
handoff before a later deployment restores the original configuration refusal;
it does not change the installed key. Synthetic tests cover both different and
absent vault values, read failure/empty/malformed input, guard denial, output
redaction and input cleanup. Live final-pair acceptance is still required.
