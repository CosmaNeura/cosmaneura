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
of the default path. Recovery before use is to omit the optional input; no
installed credential or database rollback is part of this source amendment.
