# Staging API command contract

Tier 1. Deployment v21 successfully migrated staging from `89c31f0ae275` to `9a2399212488`, removed its transient job, and made API, worker and scheduler healthy on the selected candidate digest. Its final evidence check incorrectly required empty API arguments while Azure correctly retained the Procfile command `python main.py`.

Read the API command from the same immutable candidate Procfile used for worker and scheduler. Set it explicitly during API updates and require that exact command during live-state collection and final candidate verification. This also prevents a future rollout from silently inheriting an unrelated API override or the image's worker default. Production is unchanged.

Validate real Bash command projection for all three roles and reject empty or unexpected API arguments in exact evidence. Require independent review of the final pushed commit. Reconcile the already deployed state with read-only checks; do not repeat the migration or roll workloads merely to repair an evidence expectation. Recovery is a code revert; retain the successful schema and current healthy workload revisions.
