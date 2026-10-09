# Reuse a published staging image with a fresh plan

Tier 2: staging release evidence. Core512 was published within its plan window,
but configuration resolution failed before activation and the plan expired.
The immutable source tag prevents republishing. Retain the original plan and
publication byte-for-byte; never edit their clocks or replace the image tag.

A version-2 publication-reuse envelope binds the fresh plan hash to both original
files and hashes. The release contract validates the original successful
publication against its original plan and publication window. It requires equal
candidate/PR, source archive, founder identity, build engine, frontend/material/
rehearsal hashes, and the complete runtime/schema/recovery selection. It rejects
recursive reuse. The current protected controller may advance independently;
the unchanged deployer authenticates its source/review and fresh clock before
credentials and every protected release phase. No expired plan is executed.

Prepare a fresh plan with current live observations and the existing planner.
Run the controller's check-only and verify-source operations. Then run
`staging_release_contract.py --reuse-publication FRESH HASH ORIGINAL HASH PUBLICATION HASH`.
Save the new envelope exclusively and supply it as the deployment receipt.
This operation only validates local evidence; it does not build, publish, or
mutate a provider. The existing deployer checks the registry digest and source,
all three live recovery targets, configuration and identities before rollout.
No application source or schema changes are introduced by this controller PR.

Recovery resolves the same retained envelope to the original immutable image.
Recovery availability remains independent of plan expiry. This does not make
older application readers compatible with new records: for the recording-region
release, retain expanded readers and use forward recovery after empty-region
admissions. No database downgrade, recording/key reset or production action.

Local validation covers unchanged legacy receipts, controller-only renewal,
tampered files and plan hashes, changed source/build/runtime/recovery inputs,
failed publication, nested reuse, publication outside the original window,
existing clock/source gates and recovery behavior. Review is local implementer
review under the owner's September25 policy, not independent or GitHub approval.
