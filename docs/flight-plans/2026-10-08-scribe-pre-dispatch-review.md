# Scribe fresh authorization before dispatch

Tier 2: provider recording authorization path. Owner-directed latency work
and existing local validation/merge authority apply.

Configured key/capability operations validate consent through creator review.
That helper reauthorizes after network I/O, then its caller immediately repeats
the authorization with no intervening await. Move the helper's fresh decision
after synchronous receipt validation and remove only that overlapping caller
decision for configured, unprofiled encryption-key/capability reads. Changes
during validation still deny before the next dispatch. Other reads, all
mutations, legacy status and creator-window branches retain their caller decision; all
profile checks, component-entry checks and final publication fences remain.

Compare actual native receipts, downstream order and remaining row-lock order
with the previous duplicate checks. Commit independent identity revocation or
cancel the request during each creator-review validation; require no later
remote read or partial metadata. Retain remote-race, scope-switch, expiry,
deadline, offboarding, view-as and recording-window regressions.

Run applicable local checks and implementer review on the exact final commit,
record evidence in the PR, then merge passing work. Any staging release uses
the existing approved immutable merged-source controller and retained recovery
images, without schema, configuration, permission or cohort changes. Compare
installed preparation timings; physical audio remains excluded and virtual
audio is the authorized recording test method.
