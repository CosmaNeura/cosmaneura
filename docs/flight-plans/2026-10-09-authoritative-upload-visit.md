# Authoritative Upload Visit

Tier 2: existing clinical-upload registration path. The owner accepted the
review recommendations and authorized testing, deconfliction and merge.

Return the existing durable appointment ID from registration, including blob-key
replay. Grant consumption, actor/issuer binding, receipt completion, queueing,
auth, consent and storage boundaries remain unchanged. No migration applies.
The browser navigates only to this ID, never a difference between cached lists.
An older receiver's empty response remains successful with manual list recovery.
No patient content, upload token or new identifiers are logged.

Keep mobile form and upload progress changes already on master. Processing polls
are bounded through the existing read-deadline helper and an abortable timer;
rejections recover on a later poll and unmount cancels/ignores pending results.

Qualification: synthetic command/route contract and replay tests, upload-grant
regressions, full frontend suite, types/lint, and the local change budget. No
live Azure, Clerk, patient-data or deployment acceptance is claimed. Receiver
response is additive for older clients; newer clients fail safe if it is absent.
Recovery: revert the merge commit. No data rollback or schema contraction.

The checked-in frontend OpenAPI input must include this response reference and
model; the contract test compares those two stored sections with the receiver.
Keep unrelated historical schema differences out of this correction.
